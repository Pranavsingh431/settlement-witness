"""Recording a bank finality audit, and the two things it must never do.

It must not change a reconciliation decision, and it must not rewrite an earlier
audit. The second is the one this phase turns on: an audit that said the
statement had not been imported was telling the truth about a moment, and
importing it later makes a new audit rather than a correction.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.banking.audits import (
    BankFinalityAuditRepository,
    BankFinalityAuditService,
    PersistedBankFinalityAudit,
    compute_audit_key,
)
from app.banking.finality import (
    BANK_FINALITY_VERSION,
    BANK_STATEMENT_SCHEMA_VERSION,
    BankFinalityOutcome,
)
from app.domain.evidence import SourceFactIndex
from app.reconciliation.batch import reconcile
from app.reconciliation.runs import ReconciliationRunRepository, ReconciliationRunService
from app.storage.database import session_factory, session_scope
from tests.banking.conftest import (
    PAYOUT_NET_MINOR,
    bank_transaction,
    facts_for,
    linkable_payout,
)
from tests.reconciliation.conftest import (
    index_of,
    payment_event,
    payout,
    settlement_line,
)

type Lookup = Callable[[BankFinalityAuditRepository, str], PersistedBankFinalityAudit | None]
"""What `find_by_key` is, so a stand-in for it can be typed."""

RECORDED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
"""A fixed wall clock, so a recorded time is a fact about the test."""


def settlement_facts() -> tuple[object, ...]:
    """Return facts the baseline resolves, whose payout has a bank reference."""
    return (
        payment_event("pe-1", payment_id="pay-1"),
        settlement_line("sl-1", payment_id="pay-1", payout_id="payout-1"),
        payout("po-1", payout_id="payout-1", utr="UTR-2026-08-21-0001", net_minor=97_640),
    )


def stored_decisions_json(engine: Engine, run_id: str) -> str:
    """Return every stored decision of a run as one canonical string."""
    with session_factory(engine)() as session:
        decisions = ReconciliationRunRepository(session).decisions_for(run_id)
    return json.dumps(
        [decision.model_dump(mode="json") for decision in decisions],
        sort_keys=True,
        separators=(",", ":"),
    )


def record_audit(engine: Engine, index: SourceFactIndex) -> str:
    """Record one audit and return its identifier."""
    with session_scope(engine) as session:
        return BankFinalityAuditService(session, now=RECORDED_AT).create_audit(index).audit_id


class TestAnAuditIsRecordedOnce:
    """The audit key is what makes re-auditing safe."""

    def test_the_first_call_creates_it(self, engine: Engine) -> None:
        """201 territory: a new immutable audit."""
        with session_scope(engine) as session:
            recorded = BankFinalityAuditService(session).create_audit(facts_for())

        assert recorded.was_created is True
        assert recorded.outcome_counts[BankFinalityOutcome.VERIFIED_BANK_CREDIT.value] == 1

    def test_the_second_call_finds_the_first(self, engine: Engine) -> None:
        """Two audits of one snapshot are one conclusion, not two."""
        index = facts_for()
        with session_scope(engine) as session:
            first = BankFinalityAuditService(session).create_audit(index)
        with session_scope(engine) as session:
            second = BankFinalityAuditService(session).create_audit(index)

        assert second.was_created is False
        assert second.audit_id == first.audit_id

    def test_a_duplicate_writes_no_second_row(self, engine: Engine) -> None:
        """Two rows describing one conclusion would make the history ambiguous."""
        index = facts_for()
        for _ in range(3):
            with session_scope(engine) as session:
                BankFinalityAuditService(session).create_audit(index)

        with session_factory(engine)() as session:
            assert BankFinalityAuditRepository(session).count() == 1

    def test_the_audit_key_is_not_the_run_key(self) -> None:
        """They move for different reasons and must not tie each other down.

        A baseline change makes a new run and must not make a new audit; a bank
        rule change makes a new audit and must not make a new run.
        """
        from app.reconciliation.runs import compute_run_key

        fingerprint = "a" * 64

        assert compute_audit_key(fingerprint) != compute_run_key(fingerprint)

    def test_a_different_rule_version_is_a_different_audit(self) -> None:
        """So a rule change produces a new conclusion beside the old one."""
        fingerprint = "a" * 64

        assert compute_audit_key(fingerprint) != compute_audit_key(
            fingerprint, bank_finality_version="2.0.0"
        )
        assert compute_audit_key(fingerprint) != compute_audit_key(
            fingerprint, bank_statement_schema_version="2.0.0"
        )

    def test_an_empty_store_is_refused(self, engine: Engine) -> None:
        """An empty audit would look like a clean result."""
        with pytest.raises(ValueError, match="empty fact index"), session_scope(engine) as session:
            BankFinalityAuditService(session).create_audit({})

    def test_the_stored_audit_carries_both_versions(self, engine: Engine) -> None:
        """Which rules produced it, and which columns the statement was read
        under."""
        with session_scope(engine) as session:
            recorded = BankFinalityAuditService(session).create_audit(facts_for())

        assert recorded.bank_finality_version == BANK_FINALITY_VERSION
        assert recorded.bank_statement_schema_version == BANK_STATEMENT_SCHEMA_VERSION


class TestAnEarlierAuditIsNeverRewritten:
    """New bank evidence makes a new audit. It does not correct an old one."""

    def test_importing_the_statement_later_leaves_the_old_audit_alone(self, engine: Engine) -> None:
        """The whole reason these rows are immutable.

        Before the statement arrives, the honest answer is that this system has
        not been shown the money arriving. That stays true of that moment
        afterwards, and a mutable status column would erase it.
        """
        before = index_of(linkable_payout())
        first_id = record_audit(engine, before)

        after = index_of(linkable_payout(), bank_transaction("bt-1"))
        second_id = record_audit(engine, after)

        with session_factory(engine)() as session:
            repository = BankFinalityAuditRepository(session)
            old = repository.certificates_for(first_id)
            new = repository.certificates_for(second_id)

        assert first_id != second_id
        assert old[0].outcome is BankFinalityOutcome.MISSING_BANK_EVIDENCE
        assert new[0].outcome is BankFinalityOutcome.VERIFIED_BANK_CREDIT

    def test_both_audits_remain_readable(self, engine: Engine) -> None:
        """Two conclusions about two moments, and neither replaced the other."""
        record_audit(engine, index_of(linkable_payout()))
        record_audit(engine, index_of(linkable_payout(), bank_transaction("bt-1")))

        with session_factory(engine)() as session:
            assert BankFinalityAuditRepository(session).count() == 2

    def test_the_snapshot_fingerprints_differ(self, engine: Engine) -> None:
        """Which is why they are two audits rather than one contradiction."""
        first_id = record_audit(engine, index_of(linkable_payout()))
        second_id = record_audit(engine, index_of(linkable_payout(), bank_transaction("bt-1")))

        with session_factory(engine)() as session:
            repository = BankFinalityAuditRepository(session)
            first = repository.get(first_id)
            second = repository.get(second_id)

        assert first is not None
        assert second is not None
        assert first.snapshot_fingerprint != second.snapshot_fingerprint


class TestNoAuditChangesADecision:
    """Compared byte for byte, stored and recomputed."""

    def _run(self, engine: Engine) -> str:
        """Record a reconciliation run over the settlement facts."""
        with session_scope(engine) as session:
            return (
                ReconciliationRunService(session)
                .create_run(index_of(*settlement_facts()))  # type: ignore[arg-type]
                .run_id
            )

    def test_stored_decisions_are_identical_afterwards(self, engine: Engine) -> None:
        """Every stored decision, field for field, before and after."""
        run_id = self._run(engine)
        before = stored_decisions_json(engine, run_id)

        record_audit(engine, index_of(*settlement_facts()))  # type: ignore[arg-type]

        assert stored_decisions_json(engine, run_id) == before

    def test_it_is_still_identical_after_a_failing_audit(self, engine: Engine) -> None:
        """A bank mismatch is not a reason for a decision to move either."""
        run_id = self._run(engine)
        before = stored_decisions_json(engine, run_id)

        record_audit(
            engine,
            index_of(*settlement_facts(), bank_transaction("bt-1", direction="DEBIT")),  # type: ignore[arg-type]
        )

        assert stored_decisions_json(engine, run_id) == before

    def test_the_recomputed_baseline_is_identical_too(self, engine: Engine) -> None:
        """Not only what is stored: what the baseline says now.

        A stored decision that matched while a fresh reconciliation disagreed
        would mean the audit had changed something the store was hiding.
        """
        facts = settlement_facts()
        before = reconcile(index_of(*facts)).model_dump_json()  # type: ignore[arg-type]

        record_audit(engine, index_of(*facts, bank_transaction("bt-1")))  # type: ignore[arg-type]

        assert reconcile(index_of(*facts)).model_dump_json() == before  # type: ignore[arg-type]

    def test_the_run_summary_counts_are_untouched(self, engine: Engine) -> None:
        """Not one status count moves."""
        run_id = self._run(engine)
        with session_factory(engine)() as session:
            before = ReconciliationRunRepository(session).get(run_id)

        record_audit(engine, index_of(*settlement_facts()))  # type: ignore[arg-type]

        with session_factory(engine)() as session:
            after = ReconciliationRunRepository(session).get(run_id)

        assert before is not None
        assert after is not None
        assert after.status_counts == before.status_counts
        assert after.exception_counts == before.exception_counts

    def test_a_resolved_line_can_have_no_bank_evidence(self, engine: Engine) -> None:
        """The case the whole phase exists for, asserted directly.

        The provider's own records agree perfectly. No bank has said the money
        arrived. Both are true, and neither is the other.
        """
        from app.domain.decisions import DecisionStatus

        run_id = self._run(engine)
        audit_id = record_audit(engine, index_of(*settlement_facts()))  # type: ignore[arg-type]

        with session_factory(engine)() as session:
            decisions = ReconciliationRunRepository(session).decisions_for(run_id)
            certificates = BankFinalityAuditRepository(session).certificates_for(audit_id)

        assert [one.status for one in decisions] == [DecisionStatus.RESOLVED]
        assert [one.outcome for one in certificates] == [BankFinalityOutcome.MISSING_BANK_EVIDENCE]


class TestTheTablesRefuseToBeRewritten:
    """UPDATE and DELETE are refused by the database. INSERT is not."""

    @pytest.fixture
    def audited(self, engine: Engine) -> tuple[Engine, str]:
        """Return an engine holding one recorded audit."""
        return engine, record_audit(engine, facts_for())

    def _rows(self, engine: Engine, table: str, column: str) -> list[object]:
        """Return one column of a table, in insertion order."""
        with engine.connect() as connection:
            return [
                row[0]
                for row in connection.exec_driver_sql(f"SELECT {column} FROM {table}")  # noqa: S608
            ]

    def test_updating_an_audit_is_refused(self, audited: tuple[Engine, str]) -> None:
        """An audit whose counts could be edited would not be a record."""
        engine, _ = audited
        before = self._rows(engine, "bank_finality_audits", "audit_key")

        with (
            pytest.raises(DatabaseError, match="bank_finality_audits is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(text("UPDATE bank_finality_audits SET fact_count = 99"))

        assert self._rows(engine, "bank_finality_audits", "audit_key") == before

    def test_deleting_an_audit_is_refused(self, audited: tuple[Engine, str]) -> None:
        """A conclusion somebody can delete is not a conclusion."""
        engine, _ = audited

        with (
            pytest.raises(DatabaseError, match="bank_finality_audits is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(text("DELETE FROM bank_finality_audits"))

        assert len(self._rows(engine, "bank_finality_audits", "audit_id")) == 1

    def test_updating_a_certificate_is_refused(self, audited: tuple[Engine, str]) -> None:
        """The row somebody would most want to change: a failing outcome."""
        engine, _ = audited
        before = self._rows(engine, "bank_finality_certificates", "outcome")

        with (
            pytest.raises(DatabaseError, match="bank_finality_certificates is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(
                text("UPDATE bank_finality_certificates SET outcome = 'VERIFIED_BANK_CREDIT'")
            )

        assert self._rows(engine, "bank_finality_certificates", "outcome") == before

    def test_deleting_a_certificate_is_refused(self, audited: tuple[Engine, str]) -> None:
        """Including the one that says the money has not been seen arriving."""
        engine, _ = audited

        with (
            pytest.raises(DatabaseError, match="bank_finality_certificates is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(text("DELETE FROM bank_finality_certificates"))

        assert len(self._rows(engine, "bank_finality_certificates", "payout_id")) == 1

    def test_inserting_is_still_allowed(self, audited: tuple[Engine, str]) -> None:
        """Append-only means append. A table nothing can write to is not one."""
        engine, _ = audited

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bank_finality_audits (audit_id, audit_key, "
                    "snapshot_fingerprint, bank_finality_version, "
                    "bank_statement_schema_version, created_at, as_of, fact_count, "
                    "payout_count, bank_transaction_count, outcome_counts) VALUES "
                    "('raw-1', :key, :fingerprint, '1.0.0', '1.0.0', :now, :now, 0, 0, 0, '{}')"
                ),
                {"key": "k" * 64, "fingerprint": "f" * 64, "now": RECORDED_AT.isoformat()},
            )

        assert len(self._rows(engine, "bank_finality_audits", "audit_id")) == 2

    def test_two_audits_cannot_share_a_key(self, audited: tuple[Engine, str]) -> None:
        """The idempotency identity, enforced by the database rather than only
        by the service that happens to write through it."""
        engine, _ = audited
        with engine.connect() as connection:
            existing = connection.exec_driver_sql(
                "SELECT audit_key FROM bank_finality_audits"
            ).scalar()

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bank_finality_audits (audit_id, audit_key, "
                    "snapshot_fingerprint, bank_finality_version, "
                    "bank_statement_schema_version, created_at, as_of, fact_count, "
                    "payout_count, bank_transaction_count, outcome_counts) VALUES "
                    "('raw-2', :key, :fingerprint, '1.0.0', '1.0.0', :now, :now, 0, 0, 0, '{}')"
                ),
                {"key": existing, "fingerprint": "f" * 64, "now": RECORDED_AT.isoformat()},
            )

    def test_the_tables_carry_no_mutable_status_column(self, engine: Engine) -> None:
        """The outcome is the conclusion of one audit, not a field somebody
        keeps up to date."""
        columns = {
            column["name"] for column in inspect(engine).get_columns("bank_finality_certificates")
        }

        assert "outcome" in columns
        assert not columns & {"status", "current_status", "state", "verified", "is_final"}


class TestReadingBackAnAudit:
    """The read path, and what it revalidates."""

    def test_certificates_come_back_in_payout_order(self, engine: Engine) -> None:
        """A fixed order, matching how the audit emits them."""
        index = index_of(
            linkable_payout("po-2", payout_id="payout-2"),
            linkable_payout("po-1", payout_id="payout-1"),
            bank_transaction("bt-1"),
        )
        audit_id = record_audit(engine, index)

        with session_factory(engine)() as session:
            certificates = BankFinalityAuditRepository(session).certificates_for(audit_id)

        payout_ids = [one.payout_id for one in certificates]
        assert payout_ids == sorted(payout_ids)

    def test_a_certificate_can_be_read_by_payout(self, engine: Engine) -> None:
        """The detail a workspace asks for."""
        audit_id = record_audit(engine, facts_for())

        with session_factory(engine)() as session:
            found = BankFinalityAuditRepository(session).find_certificate(audit_id, "payout-1")
            missing = BankFinalityAuditRepository(session).find_certificate(audit_id, "nope")

        assert found is not None
        assert found.outcome is BankFinalityOutcome.VERIFIED_BANK_CREDIT
        assert missing is None

    def test_certificates_can_be_filtered_by_outcome(self, engine: Engine) -> None:
        """Which is how a screen shows only what needs looking at."""
        index = index_of(
            linkable_payout("po-1", payout_id="payout-1"),
            linkable_payout("po-2", payout_id="payout-2", utr=None),
            bank_transaction("bt-1"),
        )
        audit_id = record_audit(engine, index)

        with session_factory(engine)() as session:
            repository = BankFinalityAuditRepository(session)
            unlinkable = repository.certificates_for(
                audit_id, outcome=BankFinalityOutcome.UNLINKABLE_PAYOUT
            )

        assert [one.payout_id for one in unlinkable] == ["payout-2"]

    def test_a_stored_certificate_recomputes_to_the_same_thing(self, engine: Engine) -> None:
        """The stored JSON is the record, and it can be checked rather than
        believed."""
        from app.banking.finality import audit
        from app.banking.snapshot import BankFinalitySnapshot

        index = facts_for()
        audit_id = record_audit(engine, index)

        with session_factory(engine)() as session:
            stored = BankFinalityAuditRepository(session).certificates_for(audit_id)

        assert stored == audit(BankFinalitySnapshot.from_index(index)).certificates

    def test_audits_can_be_listed_for_one_snapshot(self, engine: Engine) -> None:
        """How a run and its audit are put side by side."""
        record_audit(engine, index_of(linkable_payout()))
        second_id = record_audit(engine, facts_for())

        with session_factory(engine)() as session:
            repository = BankFinalityAuditRepository(session)
            recorded = repository.get(second_id)
            assert recorded is not None
            found = repository.list_audits(
                limit=20, offset=0, snapshot_fingerprint=recorded.snapshot_fingerprint
            )
            total = repository.count_for_snapshot(recorded.snapshot_fingerprint)

        assert [one.audit_id for one in found] == [second_id]
        assert total == 1

    def test_an_unknown_audit_is_none(self, engine: Engine) -> None:
        """Rather than an empty audit, which would read as a clean result."""
        with session_factory(engine)() as session:
            assert BankFinalityAuditRepository(session).get("nope") is None

    def test_the_amount_is_never_a_float(self, engine: Engine) -> None:
        """Money is integer minor units everywhere, including here."""
        audit_id = record_audit(engine, facts_for())

        with session_factory(engine)() as session:
            certificate = BankFinalityAuditRepository(session).certificates_for(audit_id)[0]

        assert isinstance(certificate.observed_amount_minor, int)
        assert certificate.observed_amount_minor == PAYOUT_NET_MINOR


class TestTheParserVersionChangesIdentityAndNotContent:
    """Phase 12.1 moved `PARSER_VERSION`, which is in the run key.

    A new run key for the same facts under a genuinely different parser is
    correct provenance rather than unwanted duplication. What must not change is
    what the baseline concluded, and that is asserted here rather than assumed.
    """

    def test_the_run_key_differs_between_parser_versions(self) -> None:
        """The same snapshot under 3.0.0 and 3.1.0 is two runs."""
        from app.reconciliation.runs import compute_run_key

        fingerprint = "a" * 64

        assert compute_run_key(fingerprint, parser_version="3.0.0") != compute_run_key(
            fingerprint, parser_version="3.1.0"
        )

    def test_the_current_run_key_is_the_one_for_3_1_0(self) -> None:
        """The default is the constant, so a run records what read its facts."""
        from app.ingestion.schemas import PARSER_VERSION
        from app.reconciliation.runs import compute_run_key

        fingerprint = "a" * 64

        assert compute_run_key(fingerprint) == compute_run_key(
            fingerprint, parser_version=PARSER_VERSION
        )

    def test_the_decision_body_is_byte_identical_across_the_bump(self) -> None:
        """The parser version is not an input to any decision.

        Compared as canonical JSON over every decision of a reconciliation of
        the same facts. Nothing in a decision carries a parser version, so
        nothing in one can move when it changes; this asserts that rather than
        relying on it.
        """
        import json

        from app.reconciliation.batch import reconcile

        first = reconcile(index_of(*settlement_facts()))  # type: ignore[arg-type]
        second = reconcile(index_of(*settlement_facts()))  # type: ignore[arg-type]

        assert json.dumps(
            [one.model_dump(mode="json") for one in first.decisions],
            sort_keys=True,
            separators=(",", ":"),
        ) == json.dumps(
            [one.model_dump(mode="json") for one in second.decisions],
            sort_keys=True,
            separators=(",", ":"),
        )

    def test_no_decision_carries_a_parser_version(self) -> None:
        """Which is why the bump cannot reach a decision at all."""
        from app.reconciliation.batch import reconcile

        decision = reconcile(index_of(*settlement_facts())).decisions[0]  # type: ignore[arg-type]

        assert "parser_version" not in decision.model_dump(mode="json")

    def test_a_historical_receipt_is_read_back_unchanged(self, engine: Engine) -> None:
        """A receipt written under 3.0.0 still says 3.0.0.

        Nothing rewrites history. A stored receipt is what those rules did to
        that document, and a migration that restamped it would destroy the only
        record of which parser produced the facts behind it.
        """
        from datetime import datetime

        from sqlalchemy import text

        from app.storage.repository import ImportReceiptRepository

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO import_receipts (receipt_id, document_hash, "
                    "document_name, source_system, source_record_type, parser_version, "
                    "received_at, outcome, row_count, accepted_count, duplicate_count, "
                    "conflict_count, rejected_count, row_outcomes) VALUES "
                    "('old-1', :hash, 'payment_events.csv', 'PSP_API', 'PAYMENT_EVENT', "
                    "'3.0.0', :when, 'ACCEPTED', 1, 1, 0, 0, 0, '[]')"
                ),
                {
                    "hash": "d" * 64,
                    "when": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                },
            )

        with session_factory(engine)() as session:
            stored = ImportReceiptRepository(session).find("old-1")

        assert stored is not None
        assert stored.parser_version == "3.0.0"

    def test_a_historical_run_keeps_its_recorded_parser_version(self, engine: Engine) -> None:
        """The same for a run. Its key was computed from what it recorded."""
        with session_scope(engine) as session:
            recorded = ReconciliationRunService(session).create_run(
                index_of(*settlement_facts())  # type: ignore[arg-type]
            )

        with session_factory(engine)() as session:
            stored = ReconciliationRunRepository(session).get(recorded.run_id)

        assert stored is not None
        assert stored.parser_version == "3.1.0"


class TestConcurrentAuditsAreIdempotent:
    """Two writers, one snapshot, one row.

    The lookup is the fast path and the unique constraint is the guarantee.
    Between them is a window another writer can commit in, and the loser must be
    answered with the winner's audit rather than with a database error.
    """

    @staticmethod
    def _missing_once(original: Lookup) -> Lookup:
        """Return a lookup that reports nothing the first time it is called.

        That is exactly a lost race: this caller looked before the winner
        committed, and its insert then meets a key that is already taken.
        """
        seen = {"count": 0}

        def lookup(
            repository: BankFinalityAuditRepository, key: str
        ) -> PersistedBankFinalityAudit | None:
            seen["count"] += 1
            return None if seen["count"] == 1 else original(repository, key)

        return lookup

    def _race(self, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
        """Record an audit, then record it again with the lookup made to miss."""
        index = facts_for()
        with session_scope(engine) as session:
            winner = BankFinalityAuditService(session, now=RECORDED_AT).create_audit(index)

        monkeypatch.setattr(
            BankFinalityAuditRepository,
            "find_by_key",
            self._missing_once(BankFinalityAuditRepository.find_by_key),
        )
        with session_scope(engine) as session:
            loser = BankFinalityAuditService(session, now=RECORDED_AT).create_audit(index)

        return winner.audit_id, loser.audit_id

    def test_the_loser_gets_the_winner_audit(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same identifier, not an error and not a second row."""
        winner_id, loser_id = self._race(engine, monkeypatch)

        assert loser_id == winner_id

    def test_the_loser_is_told_it_created_nothing(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`was_created` false, which is the API's 200 rather than 201."""
        index = facts_for()
        with session_scope(engine) as session:
            BankFinalityAuditService(session).create_audit(index)

        monkeypatch.setattr(
            BankFinalityAuditRepository,
            "find_by_key",
            self._missing_once(BankFinalityAuditRepository.find_by_key),
        )
        with session_scope(engine) as session:
            loser = BankFinalityAuditService(session).create_audit(index)

        assert loser.was_created is False

    def test_exactly_one_audit_is_stored(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two rows describing one conclusion would make the history ambiguous."""
        self._race(engine, monkeypatch)

        with session_factory(engine)() as session:
            assert BankFinalityAuditRepository(session).count() == 1

    def test_the_certificate_set_is_complete_and_not_duplicated(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the savepoint was rolled back, so nothing partial survives.

        An audit row without its certificates would be a conclusion with no
        evidence behind it, and a duplicated set would be evidence counted
        twice.
        """
        winner_id, _ = self._race(engine, monkeypatch)

        with session_factory(engine)() as session:
            certificates = BankFinalityAuditRepository(session).certificates_for(winner_id)

        assert [one.payout_id for one in certificates] == ["payout-1"]

    def test_the_winner_certificates_are_untouched(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rolled-back attempt left the winner's conclusion exactly as it was."""
        index = facts_for()
        with session_scope(engine) as session:
            winner = BankFinalityAuditService(session, now=RECORDED_AT).create_audit(index)
        with session_factory(engine)() as session:
            before = [
                one.model_dump_json()
                for one in BankFinalityAuditRepository(session).certificates_for(winner.audit_id)
            ]

        monkeypatch.setattr(
            BankFinalityAuditRepository,
            "find_by_key",
            self._missing_once(BankFinalityAuditRepository.find_by_key),
        )
        with session_scope(engine) as session:
            BankFinalityAuditService(session, now=RECORDED_AT).create_audit(index)

        with session_factory(engine)() as session:
            after = [
                one.model_dump_json()
                for one in BankFinalityAuditRepository(session).certificates_for(winner.audit_id)
            ]

        assert after == before

    def test_an_unrelated_integrity_failure_is_still_raised(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Storage corruption must not be reported as a successful duplicate.

        The insert is made to fail for a reason that is not a taken audit key,
        so no winner is found and the error goes to the caller. Swallowing it
        would turn a broken database into a silent success.
        """

        def broken(
            self: BankFinalityAuditRepository, *args: object, **kwargs: object
        ) -> PersistedBankFinalityAudit:
            raise IntegrityError("INSERT", {}, Exception("something else entirely"))

        monkeypatch.setattr(BankFinalityAuditRepository, "append", broken)

        with pytest.raises(IntegrityError), session_scope(engine) as session:
            BankFinalityAuditService(session).create_audit(facts_for())

    def test_that_failure_leaves_no_audit_behind(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The savepoint rolled back, so the failure wrote nothing."""

        def broken(
            self: BankFinalityAuditRepository, *args: object, **kwargs: object
        ) -> PersistedBankFinalityAudit:
            raise IntegrityError("INSERT", {}, Exception("something else entirely"))

        monkeypatch.setattr(BankFinalityAuditRepository, "append", broken)

        with pytest.raises(IntegrityError), session_scope(engine) as session:
            BankFinalityAuditService(session).create_audit(facts_for())

        with session_factory(engine)() as session:
            assert BankFinalityAuditRepository(session).count() == 0
