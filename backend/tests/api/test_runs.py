"""Tests for persisting reconciliation runs."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from app.domain.decisions import DecisionStatus, verify_decision
from app.domain.facts import SourceRecordType
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.ingestion.schemas import PARSER_VERSION
from app.reconciliation.batch import BASELINE_VERSION, reconcile
from app.reconciliation.runs import (
    PersistedRun,
    ReconciliationRunRepository,
    ReconciliationRunService,
    compute_run_key,
)
from app.storage.database import session_factory, session_scope
from app.storage.models import ReconciliationDecisionRow, ReconciliationRunRow
from app.storage.repository import SourceFactRepository
from tests.api.conftest import import_fixtures

FROZEN_NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def create_run(engine: Engine, *, now: datetime = FROZEN_NOW) -> PersistedRun:
    """Reconcile and persist one run against a database."""
    with session_scope(engine) as session:
        return ReconciliationRunService(session, now=now).create_run(
            SourceFactRepository(session).fact_index()
        )


def row_counts(engine: Engine) -> tuple[int, int]:
    """Return how many run and decision rows are stored."""
    with session_factory(engine)() as session:
        runs = session.scalar(select(func.count()).select_from(ReconciliationRunRow))
        decisions = session.scalar(select(func.count()).select_from(ReconciliationDecisionRow))
    return int(runs or 0), int(decisions or 0)


class TestTheRunKey:
    """What makes two reconciliations the same conclusion."""

    def test_the_same_snapshot_and_versions_give_the_same_key(self) -> None:
        """Which is what makes re-running idempotent."""
        assert compute_run_key("a" * 64) == compute_run_key("a" * 64)

    def test_a_different_snapshot_gives_a_different_key(self) -> None:
        """New facts are a new conclusion."""
        assert compute_run_key("a" * 64) != compute_run_key("b" * 64)

    @pytest.mark.parametrize(
        "field", ["baseline_version", "domain_schema_version", "parser_version"]
    )
    def test_a_different_rule_version_gives_a_different_key(self, field: str) -> None:
        """The same facts under different rules can reach different conclusions.

        Treating them as one run would let a newer answer overwrite an older
        one, which is exactly what immutability is supposed to prevent.
        """
        assert compute_run_key("a" * 64) != compute_run_key("a" * 64, **{field: "99.0.0"})


class TestPersistingARun:
    """Writing a run and its decisions."""

    def test_a_run_is_persisted_with_its_decisions(self, loaded_engine: Engine) -> None:
        """One run row and one row per settlement line."""
        create_run(loaded_engine)

        assert row_counts(loaded_engine) == (1, 3)

    def test_the_run_records_every_version(self, loaded_engine: Engine) -> None:
        """A conclusion without the rules behind it cannot be interpreted."""
        run = create_run(loaded_engine)

        assert run.baseline_version == BASELINE_VERSION
        assert run.domain_schema_version == DOMAIN_SCHEMA_VERSION
        assert run.parser_version == PARSER_VERSION

    def test_the_run_records_the_snapshot_it_described(self, loaded_engine: Engine) -> None:
        """So a stored run can be tied back to the facts it saw."""
        run = create_run(loaded_engine)

        with session_factory(loaded_engine)() as session:
            batch = reconcile(SourceFactRepository(session).fact_index())
        assert run.snapshot_fingerprint == batch.snapshot_fingerprint

    def test_the_summary_counts_match_the_decisions(self, loaded_engine: Engine) -> None:
        """A summary that disagrees with its own detail is worse than none."""
        run = create_run(loaded_engine)

        assert sum(run.status_counts.values()) == run.decision_count == 3
        assert run.status_counts["RESOLVED"] == 1

    def test_the_first_write_reports_that_it_created_the_run(self, loaded_engine: Engine) -> None:
        """The API answers 201 or 200 from this, rather than guessing."""
        assert create_run(loaded_engine).was_created


class TestIdempotency:
    """Re-running the same facts records nothing new."""

    def test_a_second_run_returns_the_existing_one(self, loaded_engine: Engine) -> None:
        """Two rows describing one conclusion would make the history ambiguous."""
        first = create_run(loaded_engine)
        second = create_run(loaded_engine, now=datetime(2027, 1, 1, tzinfo=UTC))

        assert second.run_id == first.run_id
        assert not second.was_created

    def test_a_second_run_writes_no_rows(self, loaded_engine: Engine) -> None:
        """Not merely returns the same identifier."""
        create_run(loaded_engine)
        create_run(loaded_engine)

        assert row_counts(loaded_engine) == (1, 3)

    def test_the_original_created_at_is_kept(self, loaded_engine: Engine) -> None:
        """The run records when it was reached, not when it was last asked for."""
        first = create_run(loaded_engine)
        second = create_run(loaded_engine, now=datetime(2027, 1, 1, tzinfo=UTC))

        assert second.created_at == first.created_at

    def test_new_facts_create_a_new_run(self, api_engine: Engine, tmp_path: Path) -> None:
        """A changed snapshot is a different conclusion and gets its own run."""
        import_fixtures(api_engine, (("payment_events.csv", SourceRecordType.PAYMENT_EVENT),))
        first = create_run(api_engine)

        import_fixtures(api_engine, (("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),))
        second = create_run(api_engine)

        assert second.run_id != first.run_id
        assert second.snapshot_fingerprint != first.snapshot_fingerprint
        assert row_counts(api_engine)[0] == 2

    def test_the_older_run_is_untouched_by_the_newer_one(self, api_engine: Engine) -> None:
        """Immutability across runs, not only within one."""
        import_fixtures(api_engine, (("payment_events.csv", SourceRecordType.PAYMENT_EVENT),))
        first = create_run(api_engine)

        import_fixtures(api_engine, (("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),))
        create_run(api_engine)

        with session_factory(api_engine)() as session:
            stored = ReconciliationRunRepository(session).get(first.run_id)
        assert stored == first.model_copy(update={"was_created": False})


class TestStoredDecisions:
    """What comes back out."""

    def test_every_decision_round_trips_through_the_domain_model(
        self, loaded_engine: Engine
    ) -> None:
        """Stored as canonical JSON, so replay uses what was decided."""
        run = create_run(loaded_engine)

        with session_factory(loaded_engine)() as session:
            stored = ReconciliationRunRepository(session).decisions_for(run.run_id)
            live = reconcile(SourceFactRepository(session).fact_index()).decisions

        assert stored == live

    def test_stored_evidence_still_verifies_against_the_facts(self, loaded_engine: Engine) -> None:
        """A citation that no longer resolves would make the run unreplayable."""
        run = create_run(loaded_engine)

        with session_factory(loaded_engine)() as session:
            index = SourceFactRepository(session).fact_index()
            decisions = ReconciliationRunRepository(session).decisions_for(run.run_id)

        for decision in decisions:
            for reference in decision.evidence:
                fact = index[reference.source_record_id]
                assert fact.payload_hash == reference.payload_hash
                assert fact.source_system is reference.source_system

    def test_a_resolved_decision_still_resolves_on_replay(self, loaded_engine: Engine) -> None:
        """Rebuilt as a candidate and re-verified against the stored facts."""
        from app.domain.decisions import DecisionCandidate

        run = create_run(loaded_engine)
        with session_factory(loaded_engine)() as session:
            index = SourceFactRepository(session).fact_index()
            decisions = ReconciliationRunRepository(session).decisions_for(run.run_id)

        resolved = [d for d in decisions if d.status is DecisionStatus.RESOLVED]
        assert resolved
        for decision in resolved:
            replayed = verify_decision(
                DecisionCandidate(
                    decision_id=decision.decision_id,
                    subject_settlement_line_id=decision.subject_settlement_line_id,
                    linked_source_record_ids=decision.linked_source_record_ids,
                    linked_event_ids=decision.linked_event_ids,
                    evidence=decision.evidence,
                    invariant_results=decision.invariant_results,
                    exception_codes=decision.exception_codes,
                    created_at=decision.created_at,
                ),
                index,
            )
            assert replayed == decision

    def test_decisions_come_back_ordered_by_settlement_line(self, loaded_engine: Engine) -> None:
        """Matching how the baseline emits them, so two reads line up."""
        run = create_run(loaded_engine)

        with session_factory(loaded_engine)() as session:
            decisions = ReconciliationRunRepository(session).decisions_for(run.run_id)

        subjects = [d.subject_settlement_line_id for d in decisions]
        assert subjects == sorted(subjects)

    def test_an_unknown_run_has_no_decisions(self, loaded_engine: Engine) -> None:
        """Absence is a normal answer."""
        with session_factory(loaded_engine)() as session:
            assert ReconciliationRunRepository(session).decisions_for("nope") == ()

    def test_an_unknown_decision_returns_nothing(self, loaded_engine: Engine) -> None:
        """Same."""
        run = create_run(loaded_engine)
        with session_factory(loaded_engine)() as session:
            assert ReconciliationRunRepository(session).find_decision(run.run_id, "nope") is None


class TestAtomicity:
    """A run is written whole or not at all."""

    def test_a_failure_while_writing_leaves_no_partial_run(self, loaded_engine: Engine) -> None:
        """A run row with some of its decisions would be a conclusion about a
        set of lines nobody chose."""
        message = "deliberate failure after the run was appended"

        def append_then_fail() -> None:
            with session_scope(loaded_engine) as session:
                ReconciliationRunService(session, now=FROZEN_NOW).create_run(
                    SourceFactRepository(session).fact_index()
                )
                raise RuntimeError(message)

        with pytest.raises(RuntimeError, match=message):
            append_then_fail()

        assert row_counts(loaded_engine) == (0, 0)

    def test_an_empty_store_is_refused(self, api_engine: Engine) -> None:
        """An empty run would look like a clean result."""
        with pytest.raises(ValueError, match="empty fact index"):
            create_run(api_engine)

        assert row_counts(api_engine) == (0, 0)


class TestListingRuns:
    """Reading runs back."""

    def test_runs_come_back_newest_first(self, api_engine: Engine) -> None:
        """The order a person reading a history expects."""
        import_fixtures(api_engine, (("payment_events.csv", SourceRecordType.PAYMENT_EVENT),))
        older = create_run(api_engine, now=datetime(2026, 1, 1, tzinfo=UTC))
        import_fixtures(api_engine, (("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),))
        newer = create_run(api_engine, now=datetime(2026, 6, 1, tzinfo=UTC))

        with session_factory(api_engine)() as session:
            listed = ReconciliationRunRepository(session).list_runs(limit=10, offset=0)

        assert [run.run_id for run in listed] == [newer.run_id, older.run_id]

    def test_the_count_is_the_total_not_the_page(self, api_engine: Engine) -> None:
        """So a caller can tell how much more there is."""
        import_fixtures(api_engine, (("payment_events.csv", SourceRecordType.PAYMENT_EVENT),))
        create_run(api_engine)
        import_fixtures(api_engine, (("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),))
        create_run(api_engine)

        with session_factory(api_engine)() as session:
            repository = ReconciliationRunRepository(session)
            assert repository.count() == 2
            assert len(repository.list_runs(limit=1, offset=0)) == 1
