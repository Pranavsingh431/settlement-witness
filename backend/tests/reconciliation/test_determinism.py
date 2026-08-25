"""Determinism and decision authority.

Two properties this baseline has to hold, because everything downstream depends
on them. A result that reorders between runs cannot be diffed. And a result a
caller can influence is not a verdict, it is a suggestion.
"""

import json

import pytest

from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.decisions import (
    DecisionCandidate,
    DecisionStatus,
    ReconciliationDecision,
    verify_decision,
)
from app.domain.invariants import InvariantOutcome
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.reconcile_cli import render
from app.reconciliation.baseline import reconcile_line
from app.reconciliation.batch import reconcile
from app.reconciliation.snapshot import FactSnapshot, fingerprint
from tests.domain.conftest import (
    make_evidence,
    make_verification,
    passing_required_results,
)
from tests.reconciliation.conftest import (
    at,
    complete_case,
    index_of,
    payment_event,
    payout,
    settlement_line,
)


class TestByteForByteDeterminism:
    """The same facts always produce the same result."""

    def test_two_runs_render_identically(self) -> None:
        """The property the CLI output depends on."""
        index = complete_case()

        assert render(reconcile(index)) == render(reconcile(index))

    def test_two_runs_produce_equal_batches(self) -> None:
        """Not merely equal JSON. The objects themselves match."""
        index = complete_case()

        assert reconcile(index) == reconcile(index)

    def test_a_larger_batch_is_also_stable(self) -> None:
        """Several lines, several payments, still identical."""
        index = index_of(
            payment_event("pe-1"),
            payment_event("pe-2", event_id="evt-2", payment_id="pay-2"),
            payment_event("pe-3", event_id="evt-3", payment_id="pay-3"),
            settlement_line("sl-1"),
            settlement_line("sl-2", payment_id="pay-2"),
            settlement_line("sl-3", payment_id="pay-3", payout_id="payout-2"),
            payout("po-1", net_minor=195_280),
            payout("po-2", payout_id="payout-2", net_minor=97_640),
        )

        assert render(reconcile(index)) == render(reconcile(index))

    def test_decisions_are_ordered_by_settlement_line_id(self) -> None:
        """A fixed order, whatever order the facts came back in."""
        batch = reconcile(
            index_of(
                payment_event("pe-1"),
                settlement_line("sl-3"),
                settlement_line("sl-1", payment_id="pay-1"),
                settlement_line("sl-2", payment_id="pay-1"),
                payout("po-1", net_minor=292_920),
            )
        )

        subjects = [decision.subject_settlement_line_id for decision in batch.decisions]
        assert subjects == sorted(subjects)

    def test_reason_codes_are_ordered_canonically(self) -> None:
        """So two decisions with the same reasons render identically."""
        batch = reconcile(complete_case())

        for decision in batch.decisions:
            ordered = sorted(decision.reason_codes, key=lambda code: list(ReasonCode).index(code))
            assert list(decision.reason_codes) == ordered

    def test_exception_codes_are_ordered(self) -> None:
        """Same reason."""
        decision = reconcile(
            index_of(
                payment_event("pe-1"),
                payment_event(
                    "pe-2",
                    event_id="evt-2",
                    event_type="REFUND",
                    amount_minor=40_000,
                    occurred_at=at(30),
                ),
                settlement_line("sl-1", net_minor=1),
                payout("po-1", net_minor=1),
            )
        ).decisions[0]

        assert list(decision.exception_codes) == sorted(decision.exception_codes)

    def test_the_rendered_json_has_sorted_keys(self) -> None:
        """A diff should show a changed value, never a moved key."""
        rendered = render(reconcile(complete_case()))
        parsed = json.loads(rendered)

        assert list(parsed) == sorted(parsed)


class TestSnapshotFingerprint:
    """Identifies exactly which facts a run saw."""

    def test_the_same_facts_fingerprint_the_same(self) -> None:
        """Whatever order they arrive in."""
        facts = list(complete_case().values())

        assert fingerprint(facts) == fingerprint(list(reversed(facts)))

    def test_a_changed_fact_changes_the_fingerprint(self) -> None:
        """A different payload is a different snapshot."""
        before = fingerprint(list(complete_case().values()))
        after = fingerprint(
            list(index_of(payment_event("pe-1"), settlement_line("sl-1", net_minor=1)).values())
        )

        assert before != after

    def test_an_added_fact_changes_the_fingerprint(self) -> None:
        """More evidence is a different snapshot, even if no decision changes."""
        before = reconcile(complete_case()).snapshot_fingerprint
        after = reconcile(
            index_of(
                payment_event("pe-1"),
                settlement_line("sl-1"),
                payout("po-1"),
                payment_event("pe-9", event_id="evt-9", payment_id="pay-unrelated"),
            )
        ).snapshot_fingerprint

        assert before != after

    def test_as_of_comes_from_the_facts_not_the_clock(self) -> None:
        """Which is what makes a re-run reproduce the same decisions."""
        snapshot = FactSnapshot.from_index(complete_case())

        assert snapshot.as_of == max(fact.observed_at for fact in complete_case().values())


class TestDecisionAuthority:
    """A caller supplies findings. The verifier supplies the verdict."""

    def test_a_candidate_cannot_carry_reason_codes_at_all(self) -> None:
        """The field was removed rather than ignored.

        A field whose value is discarded is a lie about what a caller controls.
        """
        assert "reason_codes" not in DecisionCandidate.model_fields

    def test_supplying_reason_codes_to_a_candidate_is_refused(self) -> None:
        """Not silently dropped. Refused, because the model forbids extras."""
        import pytest
        from pydantic import ValidationError

        snapshot = FactSnapshot.from_index(complete_case())
        candidate = reconcile_line(snapshot.settlement_lines[0], snapshot)

        with pytest.raises(ValidationError):
            DecisionCandidate(
                **{
                    **candidate.model_dump(),
                    "reason_codes": (ReasonCode.NET_FORMULA_MISMATCH,),
                }
            )

    def test_reason_codes_are_a_function_of_the_backing_alone(self) -> None:
        """Two candidates with the same backing produce the same reasons.

        Whatever the callers that built them believed about the case.
        """
        index = complete_case()
        snapshot = FactSnapshot.from_index(index)
        line = snapshot.settlement_lines[0]

        first = verify_decision(reconcile_line(line, snapshot), index)
        rebuilt = DecisionCandidate(**reconcile_line(line, snapshot).model_dump())
        second = verify_decision(rebuilt, index)

        assert first.reason_codes == second.reason_codes
        assert first.status is second.status

    def test_an_injected_exception_code_cannot_manufacture_a_reason(self) -> None:
        """Exception codes are findings. They do not get to name the rule that fired.

        A caller asserting AMOUNT_MISMATCH changes the status, because a finding
        is a real input. It does not add NET_FORMULA_MISMATCH to the reasons,
        because no invariant failed.
        """
        index = complete_case()
        snapshot = FactSnapshot.from_index(index)
        candidate = reconcile_line(snapshot.settlement_lines[0], snapshot)

        injected = DecisionCandidate(
            **{
                **candidate.model_dump(),
                "exception_codes": (ExceptionCode.AMOUNT_MISMATCH,),
            }
        )
        decision = verify_decision(injected, index)

        assert decision.status is DecisionStatus.EXCEPTION
        assert ReasonCode.NET_FORMULA_MISMATCH not in decision.reason_codes
        assert ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED not in decision.reason_codes

    def test_the_engine_never_assigns_a_status(self) -> None:
        """A candidate has no status field to assign."""
        assert "status" not in DecisionCandidate.model_fields

    def test_every_decision_comes_from_verify_decision(self) -> None:
        """Which means every one carries a verification certificate."""
        batch = reconcile(complete_case())

        for decision in batch.decisions:
            assert isinstance(decision, ReconciliationDecision)
            assert len(decision.evidence_verification) == len(decision.evidence)


class TestEvidenceResolvesThroughTheIndex:
    """Every resolution is backed by facts that are really there."""

    def test_a_resolved_decision_cites_only_stored_facts(self) -> None:
        """Each citation names a record the index holds, with the right hash."""
        index = complete_case()
        batch = reconcile(index)

        for decision in batch.decisions:
            if decision.status is not DecisionStatus.RESOLVED:
                continue
            for reference in decision.evidence:
                fact = index[reference.source_record_id]
                assert fact.source_system is reference.source_system
                assert fact.payload_hash == reference.payload_hash

    def test_every_citation_is_also_a_linked_record(self) -> None:
        """Traceability, enforced by the decision model and checked here too."""
        batch = reconcile(complete_case())

        for decision in batch.decisions:
            linked = set(decision.linked_source_record_ids)
            assert {ref.source_record_id for ref in decision.evidence} <= linked


class TestSummaryCounts:
    """The batch reports what it did."""

    def test_every_status_appears_even_at_zero(self) -> None:
        """A printed zero is one somebody checked. A missing key is ambiguous."""
        batch = reconcile(complete_case())

        assert set(batch.status_counts) == {status.value for status in DecisionStatus}

    def test_status_counts_add_up_to_the_decisions(self) -> None:
        """A summary that disagrees with its own detail is worse than none."""
        batch = reconcile(
            index_of(
                payment_event("pe-1"),
                payment_event("pe-2", event_id="evt-2", payment_id="pay-2"),
                settlement_line("sl-1"),
                settlement_line("sl-2", payment_id="pay-2"),
                payout("po-1", net_minor=195_280),
            )
        )

        assert sum(batch.status_counts.values()) == len(batch.decisions)

    def test_exception_counts_match_the_decisions(self) -> None:
        """Counted per decision that carries the code."""
        batch = reconcile(
            index_of(
                payment_event("pe-1"),
                payment_event(
                    "pe-2",
                    event_id="evt-2",
                    event_type="REFUND",
                    amount_minor=40_000,
                    occurred_at=at(30),
                ),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        assert batch.exception_counts["PARTIAL_REFUND"] == 1

    def test_the_batch_records_both_versions(self) -> None:
        """A result is traceable to the rules and the contract behind it."""
        batch = reconcile(complete_case())

        assert batch.baseline_version == "1.0.0"
        assert batch.domain_schema_version == DOMAIN_SCHEMA_VERSION

    def test_resolved_count_is_exposed(self) -> None:
        """The number a reader looks for first."""
        assert reconcile(complete_case()).resolved_count == 1


class TestReasonCodesForIncompleteBacking:
    """The derivation covers the cases the engine can produce and more."""

    def test_a_required_invariant_that_never_ran_is_named(self) -> None:
        """An unrun check is unknown, and the reason says which kind of unknown."""
        index = complete_case()
        snapshot = FactSnapshot.from_index(index)
        candidate = reconcile_line(snapshot.settlement_lines[0], snapshot)

        stripped = DecisionCandidate(**{**candidate.model_dump(), "invariant_results": ()})
        decision = verify_decision(stripped, index)

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert ReasonCode.REQUIRED_INVARIANT_NOT_EVALUATED in decision.reason_codes

    def test_a_decision_with_no_evidence_says_the_evidence_is_missing(self) -> None:
        """Distinct from a citation that failed to resolve."""
        index = complete_case()
        snapshot = FactSnapshot.from_index(index)
        candidate = reconcile_line(snapshot.settlement_lines[0], snapshot)

        stripped = DecisionCandidate(
            **{
                **candidate.model_dump(),
                "evidence": (),
                "linked_source_record_ids": (),
            }
        )
        decision = verify_decision(stripped, index)

        assert ReasonCode.EVIDENCE_MISSING in decision.reason_codes


class TestTheReasonNeverNamesARuleThatDidNotFire:
    """A reason code is an account of what happened, not a placeholder.

    An earlier version fell back to REQUIRED_INVARIANT_FAILED whenever a
    decision was an exception with no other reason, which named a rule that had
    never fired. Every invariant on a partially refunded line passes; the reason
    it does not resolve is the refund.
    """

    def test_a_partial_refund_does_not_claim_an_invariant_failed(self) -> None:
        """Nothing failed. A finding was reported."""
        decision = reconcile(
            index_of(
                payment_event("pe-1"),
                payment_event(
                    "pe-2",
                    event_id="evt-2",
                    event_type="REFUND",
                    amount_minor=40_000,
                    occurred_at=at(30),
                ),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        ).decisions[0]

        assert decision.exception_codes == (ExceptionCode.PARTIAL_REFUND,)
        assert decision.reason_codes == (ReasonCode.EXCEPTION_CODE_REPORTED,)
        assert all(
            result.outcome is not InvariantOutcome.FAILED for result in decision.invariant_results
        )

    def test_a_real_invariant_failure_still_says_so(self) -> None:
        """The corrected fallback did not weaken the honest case."""
        decision = reconcile(
            index_of(
                payment_event("pe-1"),
                settlement_line("sl-1", net_minor=99_999),
                payout("po-1", net_minor=99_999),
            )
        ).decisions[0]

        assert ReasonCode.REQUIRED_INVARIANT_FAILED in decision.reason_codes
        assert ReasonCode.NET_FORMULA_MISMATCH in decision.reason_codes
        assert ReasonCode.EXCEPTION_CODE_REPORTED not in decision.reason_codes

    def test_a_status_that_does_not_follow_from_the_backing_is_refused(self) -> None:
        """Rather than filled in with a reason that never fired.

        Not reachable through the engine, which always derives the status from
        the same backing. Reachable by calling the derivation directly with a
        status that contradicts it, and refusing is the honest answer.
        """
        from app.domain.decisions import derive_reason_codes

        with pytest.raises(ValueError, match="does not follow from this backing"):
            derive_reason_codes(
                status=DecisionStatus.EXCEPTION,
                evidence=(make_evidence(),),
                invariant_results=passing_required_results(),
                exception_codes=(),
                evidence_verification=(make_verification(),),
            )
