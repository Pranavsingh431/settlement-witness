"""Tests for persistence, the append-only guarantees and the read path.

The read path matters most. Phase 1.1 built `verify_decision` around a complete
fact index and left supplying one to this phase. These tests prove storage can.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.codes import ReasonCode
from app.domain.decisions import DecisionCandidate, DecisionStatus, verify_decision
from app.domain.evidence import EvidenceRef
from app.domain.facts import IdempotencyKey, SourceRecordType, SourceSystem
from app.domain.invariants import (
    REQUIRED_FOR_RESOLUTION,
    InvariantOutcome,
    InvariantResult,
)
from app.ingestion.service import ImportOutcome, ImportService
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_factory,
    session_scope,
)
from app.storage.repository import ImportReceiptRepository, SourceFactRepository
from tests.ingestion.conftest import FIXED_NOW, read_fixture

PSP = SourceSystem.PSP_API


def load(session: Session, fixture: str, record_type: SourceRecordType) -> None:
    """Import one fixture document into the given session."""
    ImportService(session, now=FIXED_NOW).import_document(
        read_fixture(fixture),
        source_system=PSP,
        record_type=record_type,
        document_name=fixture,
    )


def load_all(session: Session) -> None:
    """Import all three documented example documents."""
    load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
    load(session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE)
    load(session, "payouts.csv", SourceRecordType.PAYOUT)


class TestSchemaSetup:
    """Creating the database from nothing."""

    def test_setup_creates_both_tables(self, tmp_path: Path) -> None:
        """A clean file gets a usable schema."""
        engine = create_database_engine(database_url_for(tmp_path / "fresh.sqlite"))
        create_schema(engine)

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 0
            assert ImportReceiptRepository(session).all_receipts() == []
        engine.dispose()

    def test_setup_is_safe_to_run_again(self, tmp_path: Path) -> None:
        """Re-running setup on a populated database changes nothing."""
        url = database_url_for(tmp_path / "twice.sqlite")
        engine = create_database_engine(url)
        create_schema(engine)
        with session_scope(engine) as session:
            load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)

        create_schema(engine)

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 5
        engine.dispose()


class TestSessionScope:
    """The unit of work helper."""

    def test_a_successful_scope_commits(self, engine: Engine) -> None:
        """Work done inside the scope is durable."""
        with session_scope(engine) as session:
            load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 5

    def test_a_failing_scope_rolls_back(self, engine: Engine) -> None:
        """An exception leaves nothing behind."""
        message = "deliberate failure"

        def import_then_fail() -> None:
            with session_scope(engine) as session:
                load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
                raise RuntimeError(message)

        with pytest.raises(RuntimeError, match=message):
            import_then_fail()

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 0


class TestPersistenceAcrossRestart:
    """Reopening the database must show the same facts and the same history."""

    def test_accepted_facts_survive_a_reopen(self, database_path: Path) -> None:
        """The point of writing to a file rather than to memory."""
        first = create_database_engine(database_url_for(database_path))
        create_schema(first)
        with session_scope(first) as session:
            load_all(session)
            before = SourceFactRepository(session).all_facts()
        first.dispose()

        second = create_database_engine(database_url_for(database_path))
        with session_factory(second)() as session:
            after = SourceFactRepository(session).all_facts()
        second.dispose()

        assert len(after) == 10
        assert after == before

    def test_audit_history_survives_a_reopen(self, database_path: Path) -> None:
        """A rejected attempt is still on the record after a restart."""
        first = create_database_engine(database_url_for(database_path))
        create_schema(first)
        with session_scope(first) as session:
            load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
            load(session, "invalid_float_money.csv", SourceRecordType.PAYMENT_EVENT)
        first.dispose()

        second = create_database_engine(database_url_for(database_path))
        with session_factory(second)() as session:
            outcomes = [r.outcome for r in ImportReceiptRepository(session).all_receipts()]
        second.dispose()

        assert outcomes == [
            ImportOutcome.ACCEPTED.value,
            ImportOutcome.REJECTED_INVALID.value,
        ]

    def test_a_reopened_database_rejects_a_conflicting_import(self, database_path: Path) -> None:
        """Idempotency is a property of the store, not of one process."""
        first = create_database_engine(database_url_for(database_path))
        create_schema(first)
        with session_scope(first) as session:
            load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        first.dispose()

        second = create_database_engine(database_url_for(database_path))
        with session_scope(second) as session:
            receipt = ImportService(session, now=FIXED_NOW).import_document(
                read_fixture("conflicting_payment_events.csv"),
                source_system=PSP,
                record_type=SourceRecordType.PAYMENT_EVENT,
                document_name="conflicting_payment_events.csv",
            )
            assert receipt.outcome is ImportOutcome.REJECTED_CONFLICT
            assert SourceFactRepository(session).count() == 5
        second.dispose()

    def test_a_reimport_after_a_reopen_is_a_no_op(self, database_path: Path) -> None:
        """Re-running the same import against a restarted database is safe."""
        first = create_database_engine(database_url_for(database_path))
        create_schema(first)
        with session_scope(first) as session:
            load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        first.dispose()

        second = create_database_engine(database_url_for(database_path))
        with session_scope(second) as session:
            receipt = ImportService(session, now=FIXED_NOW).import_document(
                read_fixture("payment_events.csv"),
                source_system=PSP,
                record_type=SourceRecordType.PAYMENT_EVENT,
                document_name="payment_events.csv",
            )
            assert receipt.outcome is ImportOutcome.DUPLICATE_NO_OP
        second.dispose()


class TestSourceFactRepository:
    """Reads, and the absence of any way to write over a fact."""

    def test_a_fact_round_trips_unchanged(self, session: Session) -> None:
        """What comes out is what went in, revalidated on the way."""
        load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        repository = SourceFactRepository(session)

        stored = repository.all_facts()[0]
        assert repository.get(stored.source_record_id) == stored

    def test_an_unknown_record_id_returns_nothing(self, session: Session) -> None:
        """Absence is a normal answer, not an error."""
        assert SourceFactRepository(session).get("no-such-record") is None

    def test_lookup_by_idempotency_key(self, session: Session) -> None:
        """The identity the contract defines is queryable."""
        load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)

        found = SourceFactRepository(session).find_by_idempotency_key(
            IdempotencyKey(source_system=PSP, provider_event_id="pe-0001")
        )
        assert found is not None
        assert found.provider_event_id == "pe-0001"

    def test_an_unknown_idempotency_key_returns_nothing(self, session: Session) -> None:
        """No fact holds that identity yet."""
        assert (
            SourceFactRepository(session).find_by_idempotency_key(
                IdempotencyKey(source_system=PSP, provider_event_id="never-seen")
            )
            is None
        )

    def test_facts_come_back_in_a_fixed_order(self, session: Session) -> None:
        """Two reads of one database give the same sequence."""
        load_all(session)
        repository = SourceFactRepository(session)

        assert repository.all_facts() == repository.all_facts()

    def test_the_repository_offers_no_way_to_change_a_fact(self) -> None:
        """Append-only is enforced by the absence of a method, not by a rule."""
        surface = {name for name in dir(SourceFactRepository) if not name.startswith("_")}
        assert surface == {
            "add",
            "all_facts",
            "count",
            "fact_index",
            "find_by_idempotency_key",
            "get",
        }

    def test_the_database_refuses_a_duplicate_identity(self, session: Session) -> None:
        """The idempotency identity is enforced by the schema too.

        The import service checks this before writing. The constraint is the
        second line of defence, for anything that bypasses the service.
        """
        load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        repository = SourceFactRepository(session)
        stored = repository.all_facts()[0]

        clone = stored.model_copy(update={"source_record_id": "a-different-record-id"})
        repository.add(clone)

        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


class TestFactIndexForVerification:
    """The read path Phase 1.1 was designed around."""

    def test_the_index_is_keyed_by_each_fact_s_own_record_id(self, session: Session) -> None:
        """Which is what the Phase 1.2 hardening requires of any index."""
        load_all(session)

        index = SourceFactRepository(session).fact_index()
        assert all(key == fact.source_record_id for key, fact in index.items())

    def test_the_index_holds_every_accepted_fact(self, session: Session) -> None:
        """Complete, not filtered. Completeness is what makes a result meaningful."""
        load_all(session)

        repository = SourceFactRepository(session)
        assert len(repository.fact_index()) == repository.count() == 10

    def test_the_index_cannot_be_modified_by_its_holder(self, session: Session) -> None:
        """A verifier must not be able to add the fact it is looking for."""
        load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)

        index = SourceFactRepository(session).fact_index()
        with pytest.raises(TypeError):
            index["injected"] = next(iter(index.values()))  # type: ignore[index]

    def test_a_decision_citing_a_stored_fact_resolves(self, session: Session) -> None:
        """End to end: ingestion produces evidence a verifier accepts."""
        load(session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE)
        index = SourceFactRepository(session).fact_index()
        fact = next(iter(index.values()))

        candidate = DecisionCandidate(
            decision_id="dec-1",
            subject_settlement_line_id="line-0001",
            linked_source_record_ids=(fact.source_record_id,),
            evidence=(
                EvidenceRef(
                    source_record_id=fact.source_record_id,
                    source_system=fact.source_system,
                    payload_hash=fact.payload_hash,
                ),
            ),
            invariant_results=tuple(
                InvariantResult(invariant_id=invariant_id, outcome=InvariantOutcome.PASSED)
                for invariant_id in sorted(REQUIRED_FOR_RESOLUTION, key=lambda item: item.value)
            ),
            reason_codes=(ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED,),
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
        )

        decision = verify_decision(candidate, index)

        assert decision.status is DecisionStatus.RESOLVED
        assert decision.verified_evidence_count == 1

    def test_a_partial_index_yields_insufficient_evidence_not_a_wrong_answer(
        self, session: Session
    ) -> None:
        """Why `fact_index` returns everything.

        A citation whose fact was left out resolves to nothing, so the decision
        abstains rather than resolving wrongly. Safe is not the same as correct,
        which is why completeness is storage's job rather than each caller's.
        """
        load(session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE)
        fact = next(iter(SourceFactRepository(session).fact_index().values()))

        candidate = DecisionCandidate(
            decision_id="dec-2",
            subject_settlement_line_id="line-0001",
            linked_source_record_ids=(fact.source_record_id,),
            evidence=(
                EvidenceRef(
                    source_record_id=fact.source_record_id,
                    source_system=fact.source_system,
                    payload_hash=fact.payload_hash,
                ),
            ),
            invariant_results=tuple(
                InvariantResult(invariant_id=invariant_id, outcome=InvariantOutcome.PASSED)
                for invariant_id in sorted(REQUIRED_FOR_RESOLUTION, key=lambda item: item.value)
            ),
            reason_codes=(ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED,),
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
        )

        decision = verify_decision(candidate, [])

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert ReasonCode.EVIDENCE_FACT_NOT_FOUND in decision.reason_codes


class TestImportReceiptRepository:
    """Reads over the audit trail."""

    def test_a_receipt_is_retrievable_by_its_identifier(self, session: Session) -> None:
        """The identifier a caller was handed is the one that looks it up."""
        receipt = ImportService(session, now=FIXED_NOW).import_document(
            read_fixture("payment_events.csv"),
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="payment_events.csv",
        )
        session.flush()

        found = ImportReceiptRepository(session).get(receipt.receipt_id)
        assert found is not None
        assert found.document_hash == receipt.document_hash

    def test_an_unknown_receipt_id_returns_nothing(self, session: Session) -> None:
        """Absence is a normal answer."""
        assert ImportReceiptRepository(session).get("no-such-receipt") is None

    def test_receipts_are_ordered_by_when_they_happened(self, session: Session) -> None:
        """Not by identifier, which is a random uuid, and not by a frozen clock."""
        for fixture, record_type in [
            ("payment_events.csv", SourceRecordType.PAYMENT_EVENT),
            ("invalid_float_money.csv", SourceRecordType.PAYMENT_EVENT),
            ("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),
        ]:
            load(session, fixture, record_type)
        session.flush()

        receipts = ImportReceiptRepository(session).all_receipts()
        assert [r.source_record_type for r in receipts] == [
            "PAYMENT_EVENT",
            "PAYMENT_EVENT",
            "SETTLEMENT_LINE",
        ]
        assert [r.sequence for r in receipts] == sorted(r.sequence for r in receipts)

    def test_row_outcomes_are_stored_on_the_receipt(self, session: Session) -> None:
        """An auditor can see what happened to each row, not just a total."""
        load(session, "invalid_mixed_rows.csv", SourceRecordType.PAYMENT_EVENT)
        session.flush()

        stored = ImportReceiptRepository(session).all_receipts()[0]
        assert len(stored.row_outcomes) == 3
        assert {entry["outcome"] for entry in stored.row_outcomes} == {
            "NOT_APPLIED",
            "REJECTED",
        }

    def test_the_repository_offers_no_way_to_change_a_receipt(self) -> None:
        """Append-only, like the facts."""
        surface = {name for name in dir(ImportReceiptRepository) if not name.startswith("_")}
        assert surface == {"add", "all_receipts", "for_document", "get"}


class TestDatabaseSetupCommand:
    """The command behind `make db-setup`."""

    def test_setup_creates_the_file_and_both_tables(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A clean temporary database, created from nothing."""
        from app.db_setup import run as setup_run

        database = tmp_path / "nested" / "created.sqlite"
        status = setup_run(["--database", str(database)])

        printed = capsys.readouterr().out
        assert status == 0
        assert database.is_file()
        assert "source_facts" in printed
        assert "import_receipts" in printed

    def test_setup_is_safe_to_run_twice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Running it again on a populated database keeps the data."""
        from app.db_setup import run as setup_run

        database = tmp_path / "again.sqlite"
        setup_run(["--database", str(database)])

        engine = create_database_engine(database_url_for(database))
        with session_scope(engine) as session:
            load(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        engine.dispose()

        assert setup_run(["--database", str(database)]) == 0
        capsys.readouterr()

        engine = create_database_engine(database_url_for(database))
        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 5
        engine.dispose()

    def test_setup_exits_with_its_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The module entry point turns the status into an exit code."""
        import sys

        import app.db_setup as setup

        database = tmp_path / "exit.sqlite"
        monkeypatch.setattr(sys, "argv", ["db-setup", "--database", str(database)])

        with pytest.raises(SystemExit) as caught:
            setup.main()

        capsys.readouterr()
        assert caught.value.code == 0
        assert database.is_file()
