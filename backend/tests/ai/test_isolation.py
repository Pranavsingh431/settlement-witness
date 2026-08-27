"""Proving the shadow harness changes nothing.

This is the phase's central claim, so it is tested against a real database
holding real facts, receipts and a run, rather than against a snapshot in
isolation. Every adversarial case is run and the whole store is compared before
and after.

A weaker version of these tests would count rows. These compare the records
themselves, so a value changed inside a fact would be caught as well as a fact
appearing.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.ai.candidates import build_request, truth_for
from app.ai.evaluation import evaluate
from app.ai.provider import (
    FailureKind,
    FixtureProvider,
    always_abstains,
    fails_with,
    matching_visible_references,
    returns,
    selecting,
    selects_everything,
)
from app.ai.validation import evidence_for, parse_proposal
from app.reconciliation.baseline import reconcile_all
from app.reconciliation.runs import ReconciliationRunService
from app.reconciliation.snapshot import FactSnapshot
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_factory,
    session_scope,
)
from app.storage.repository import SourceFactRepository
from tests.ai.conftest import FIXTURE, payload_for, store_state
from tests.api.conftest import FIXTURE_DOCUMENTS, import_fixtures


@pytest.fixture
def loaded_engine(tmp_path: Path) -> Iterator[Engine]:
    """Return a database holding the example documents and one recorded run."""
    engine = create_database_engine(database_url_for(tmp_path / "shadow.sqlite"))
    create_schema(engine)
    import_fixtures(engine, FIXTURE_DOCUMENTS)
    with session_scope(engine) as session:
        ReconciliationRunService(session).create_run(SourceFactRepository(session).fact_index())
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def live_snapshot(loaded_engine: Engine) -> FactSnapshot:
    """Return the snapshot over the stored facts."""
    with session_factory(loaded_engine)() as session:
        return FactSnapshot.from_index(SourceFactRepository(session).fact_index())


def baseline_decisions(engine: Engine) -> list[str]:
    """Return every decision the baseline reaches, as JSON, for comparison."""
    with session_factory(engine)() as session:
        index = SourceFactRepository(session).fact_index()
    snapshot = FactSnapshot.from_index(index)
    return [decision.model_dump_json() for decision in reconcile_all(snapshot, index)]


def providers(snapshot: FactSnapshot) -> dict[str, FixtureProvider]:
    """Return one provider per behaviour worth proving harmless.

    Every page result a provider can produce: valid, broad, declined, malformed,
    naming a record the page did not offer, forging each piece of server-owned
    metadata, and failing three ways.
    """
    return {
        "perfect": FixtureProvider(selecting(lambda one: tuple(sorted(truth_for(one, snapshot))))),
        "matches visible references": FixtureProvider(matching_visible_references()),
        "selects everything": FixtureProvider(selects_everything()),
        "abstains": FixtureProvider(always_abstains()),
        "malformed": FixtureProvider(returns("not a proposal at all")),
        "out of page": FixtureProvider(returns(payload_for(("PAYMENT_EVENT:never-offered",)))),
        "supplies a forged identity": FixtureProvider(
            returns(
                payload_for(
                    ("PAYMENT_EVENT:pe-1",), provider={"name": "attacker", "version": "999"}
                )
            )
        ),
        "supplies another subject": FixtureProvider(
            returns(payload_for(("a",), subject_settlement_line_id="line-0002"))
        ),
        "supplies a stale fingerprint": FixtureProvider(
            returns(payload_for(("a",), snapshot_fingerprint="f" * 64))
        ),
        "supplies a page ordinal": FixtureProvider(returns(payload_for(("a",), page_ordinal=99))),
        "supplies an environment": FixtureProvider(
            returns(payload_for(("a",), environment_fingerprint="f" * 64))
        ),
        "unknown field": FixtureProvider(returns(payload_for(("a",), status="RESOLVED"))),
        "timed out": FixtureProvider(fails_with(FailureKind.TIMED_OUT)),
        "raised": FixtureProvider(fails_with(FailureKind.RAISED)),
        "returned nothing": FixtureProvider(fails_with(FailureKind.RETURNED_NOTHING)),
    }


class TestNoProposalChangesTheStore:
    """Facts, receipts and runs are identical afterwards, in every case."""

    def test_every_provider_behaviour_leaves_the_store_untouched(
        self, loaded_engine: Engine, live_snapshot: FactSnapshot
    ) -> None:
        """Valid, degenerate, invalid and failed alike.

        One test over every behaviour rather than one each, because what is
        being asserted is the same sentence about all of them and splitting it
        would invite a new behaviour being added without this check.
        """
        before = store_state(loaded_engine)

        for name, provider in providers(live_snapshot).items():
            evaluate(live_snapshot, provider)
            assert store_state(loaded_engine) == before, f"{name} changed the store"

    def test_the_facts_are_byte_identical_afterwards(
        self, loaded_engine: Engine, live_snapshot: FactSnapshot
    ) -> None:
        """Including payload hashes, so nothing was rewritten in place."""
        with session_factory(loaded_engine)() as session:
            before = [fact.model_dump_json() for fact in SourceFactRepository(session).all_facts()]

        for provider in providers(live_snapshot).values():
            evaluate(live_snapshot, provider)

        with session_factory(loaded_engine)() as session:
            after = [fact.model_dump_json() for fact in SourceFactRepository(session).all_facts()]

        assert after == before

    def test_building_evidence_from_a_valid_proposal_writes_nothing(
        self, loaded_engine: Engine, live_snapshot: FactSnapshot
    ) -> None:
        """The one path that touches facts reads them and stops."""
        request = build_request("line-0001", live_snapshot)
        result = parse_proposal(
            payload_for(tuple(sorted(truth_for(request, live_snapshot)))), request, FIXTURE
        )
        before = store_state(loaded_engine)

        from app.ai.validation import ValidProposal

        assert isinstance(result, ValidProposal)
        references = evidence_for(result, live_snapshot)

        assert references
        assert store_state(loaded_engine) == before


class TestTheBaselineIsUnaffected:
    """Reconciliation produces exactly what it produced before."""

    def test_the_decisions_are_identical_after_every_proposal(
        self, loaded_engine: Engine, live_snapshot: FactSnapshot
    ) -> None:
        """The claim that this is shadow mode, stated as a comparison.

        Every decision, field for field, including its status, its evidence and
        its reason codes.
        """
        before = baseline_decisions(loaded_engine)

        for provider in providers(live_snapshot).values():
            evaluate(live_snapshot, provider)

        assert baseline_decisions(loaded_engine) == before
        assert before

    def test_the_snapshot_fingerprint_does_not_move(self, live_snapshot: FactSnapshot) -> None:
        """Nothing in the harness adds, removes or rewrites a fact."""
        before = live_snapshot.digest

        for provider in providers(live_snapshot).values():
            evaluate(live_snapshot, provider)

        assert live_snapshot.digest == before

    def test_a_run_created_afterwards_is_the_same_run(
        self, loaded_engine: Engine, live_snapshot: FactSnapshot
    ) -> None:
        """Idempotency is untouched, so the harness changed no rule version.

        Asking for a run again returns the one already recorded. If the harness
        had altered a fact, a version or a rule, the run key would differ and a
        second run would be written.
        """
        for provider in providers(live_snapshot).values():
            evaluate(live_snapshot, provider)

        with session_scope(loaded_engine) as session:
            again = ReconciliationRunService(session).create_run(
                SourceFactRepository(session).fact_index()
            )

        assert not again.was_created


class TestNothingIsPersisted:
    """This phase stores no proposals, deliberately.

    A proposal record would be a second thing that looks like a decision. The
    shadow evaluator needs no history to compute a report, so there is nothing
    to persist, and the safest amount of model output in the database is none.
    """

    def test_no_table_holds_a_proposal(self, loaded_engine: Engine) -> None:
        """Asserted over the schema, so adding one is a deliberate act."""
        from sqlalchemy import inspect

        tables = set(inspect(loaded_engine).get_table_names())

        assert not any("proposal" in name or "ai" in name.split("_") for name in tables)

    def test_the_harness_module_imports_no_repository(self) -> None:
        """It cannot write, because it never reaches anything that can."""
        import app.ai.evaluation as harness

        source = Path(harness.__file__).read_text(encoding="utf-8")

        assert "repository" not in source.lower()
        assert "session" not in source.lower()


class TestTheGeneratedCorpusIsAlsoInert:
    """The shadow corpus is generated in memory and never reaches the database."""

    def test_evaluating_the_corpus_changes_nothing(self, loaded_engine: Engine) -> None:
        """It is its own snapshot, built from generated facts.

        Those facts are never imported, never written and never mixed with the
        stored ones. A test rather than a comment, because a benchmark that
        quietly seeded the database would corrupt every later reconciliation.
        """
        from app.ai.corpus import build_corpus

        corpus = build_corpus()
        corpus_snapshot = FactSnapshot.from_index(corpus.index)
        before = store_state(loaded_engine)

        for provider in providers(corpus_snapshot).values():
            evaluate(corpus_snapshot, provider, corpus.expected_actions, corpus.styling)

        assert store_state(loaded_engine) == before

    def test_no_corpus_fact_reaches_the_store(self, loaded_engine: Engine) -> None:
        """The generated facts and the stored ones stay separate populations."""
        from app.ai.corpus import build_corpus

        corpus = build_corpus()
        corpus_snapshot = FactSnapshot.from_index(corpus.index)
        evaluate(corpus_snapshot, FixtureProvider(always_abstains()))

        with session_factory(loaded_engine)() as session:
            stored = {fact.source_record_id for fact in SourceFactRepository(session).all_facts()}

        assert not stored & {fact.source_record_id for fact in corpus.facts}

    def test_the_oracle_is_not_persisted(self, loaded_engine: Engine) -> None:
        """No table holds a scenario, an expected action or an answer."""
        from sqlalchemy import inspect

        tables = set(inspect(loaded_engine).get_table_names())

        assert not any(
            word in name
            for name in tables
            for word in ("scenario", "oracle", "corpus", "proposal", "page")
        )
