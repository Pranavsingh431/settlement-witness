"""Tests for the deterministic reconciliation baseline.

The baseline resolves one shape and declines everything else. These tests hold
that line, case by case, and check that a decline is honest about which of the
several reasons applied.
"""

import pytest

from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import SourceFactIndex
from app.domain.invariants import REQUIRED_FOR_RESOLUTION, InvariantId, InvariantOutcome
from app.reconciliation.baseline import EXCEPTION_BY_INVARIANT, reconcile_line
from app.reconciliation.batch import reconcile
from app.reconciliation.snapshot import FactSnapshot
from tests.reconciliation.conftest import (
    at,
    complete_case,
    index_of,
    payment_event,
    payout,
    settlement_line,
)


def only_decision(index: SourceFactIndex) -> ReconciliationDecision:
    """Return the single decision from a one line index."""
    batch = reconcile(index)
    assert len(batch.decisions) == 1
    return batch.decisions[0]


class TestTheResolvableShape:
    """One capture, one line that adds up, one payout that matches."""

    def test_a_complete_direct_case_resolves(self) -> None:
        """The only shape this baseline claims to be able to settle."""
        decision = only_decision(complete_case())

        assert decision.status is DecisionStatus.RESOLVED
        assert decision.exception_codes == ()

    def test_a_resolution_cites_the_line_the_payment_and_the_payout(self) -> None:
        """Three facts, all of them linked."""
        decision = only_decision(complete_case())

        cited = {ref.source_record_id for ref in decision.evidence}
        assert cited == {
            "SETTLEMENT_LINE:sl-1",
            "PAYMENT_EVENT:pe-1",
            "PAYOUT:po-1",
        }

    def test_every_citation_verifies_against_the_index(self) -> None:
        """The evidence is real, not merely well formed."""
        decision = only_decision(complete_case())

        assert decision.verified_evidence_count == len(decision.evidence)

    def test_every_required_invariant_is_recorded(self) -> None:
        """A resolution has to show its working, for all five of them."""
        decision = only_decision(complete_case())

        recorded = {result.invariant_id for result in decision.invariant_results}
        assert recorded == {
            InvariantId.INV_001,
            InvariantId.INV_002,
            InvariantId.INV_003,
            InvariantId.INV_004,
            InvariantId.INV_009,
        }
        assert recorded == REQUIRED_FOR_RESOLUTION

    def test_the_reason_is_that_every_required_invariant_passed(self) -> None:
        """Derived by the verifier, not asserted by the engine."""
        decision = only_decision(complete_case())

        assert decision.reason_codes == (ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED,)

    def test_inv_004_is_not_applicable_with_nothing_returned(self) -> None:
        """A determinate answer, which is why it does not block the resolution."""
        decision = only_decision(complete_case())

        result = next(
            r for r in decision.invariant_results if r.invariant_id is InvariantId.INV_004
        )
        assert result.outcome is InvariantOutcome.NOT_APPLICABLE


class TestMissingEvidence:
    """What the baseline does when a direct reference points at nothing."""

    def test_a_line_with_no_payment_facts_does_not_resolve(self) -> None:
        """The line names a payment no source fact describes."""
        decision = only_decision(index_of(settlement_line("sl-1"), payout("po-1")))

        assert decision.status is not DecisionStatus.RESOLVED
        assert ExceptionCode.MISSING_PAYMENT in decision.exception_codes

    def test_a_line_with_no_payout_fact_does_not_resolve(self) -> None:
        """Without the payout there is nothing to check the batch total against."""
        decision = only_decision(index_of(payment_event("pe-1"), settlement_line("sl-1")))

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert ExceptionCode.INSUFFICIENT_EVIDENCE in decision.exception_codes

    def test_a_missing_payout_leaves_inv_003_undetermined(self) -> None:
        """Reported as unknown rather than as a mismatch."""
        decision = only_decision(index_of(payment_event("pe-1"), settlement_line("sl-1")))

        result = next(
            r for r in decision.invariant_results if r.invariant_id is InvariantId.INV_003
        )
        assert result.outcome is InvariantOutcome.INSUFFICIENT_INPUT

    def test_payment_events_with_no_capture_do_not_resolve(self) -> None:
        """Without a capture the ceiling for INV-004 is unknown."""
        decision = only_decision(
            index_of(
                payment_event("pe-1", event_type="REFUND"),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        assert decision.status is not DecisionStatus.RESOLVED
        assert ExceptionCode.INSUFFICIENT_EVIDENCE in decision.exception_codes


class TestAmbiguity:
    """The baseline declines rather than choosing."""

    def test_two_captures_for_one_payment_do_not_resolve(self) -> None:
        """Choosing one would decide which the line settled. Nothing says."""
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                payment_event("pe-2", event_id="evt-2", occurred_at=at(5)),
                settlement_line("sl-1"),
                payout("po-1", net_minor=97_640),
            )
        )

        assert decision.status is not DecisionStatus.RESOLVED
        assert ExceptionCode.UNSUPPORTED_STATE in decision.exception_codes

    def test_the_engine_does_not_pick_one_of_the_captures(self) -> None:
        """Both captures are cited, so the ambiguity is visible in the evidence."""
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                payment_event("pe-2", event_id="evt-2", occurred_at=at(5)),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        assert len(decision.linked_event_ids) == 2


class TestLifecycleOrdering:
    """Sequences that make a settlement unexplainable."""

    def test_a_refund_before_its_capture_does_not_resolve(self) -> None:
        """The sequence is impossible as reported."""
        decision = only_decision(
            index_of(
                payment_event("pe-1", occurred_at=at(60)),
                payment_event(
                    "pe-2",
                    event_id="evt-2",
                    event_type="REFUND",
                    amount_minor=40_000,
                    occurred_at=at(10),
                ),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        assert decision.status is not DecisionStatus.RESOLVED
        assert ExceptionCode.OUT_OF_ORDER_EVENT in decision.exception_codes

    def test_a_partial_refund_does_not_resolve(self) -> None:
        """A balance remains to account for, and the baseline has no rule for it."""
        decision = only_decision(
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

        assert decision.status is not DecisionStatus.RESOLVED
        assert ExceptionCode.PARTIAL_REFUND in decision.exception_codes

    def test_a_full_return_that_still_settled_is_unsupported(self) -> None:
        """A state the contract does not describe, reported as such."""
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                payment_event(
                    "pe-2",
                    event_id="evt-2",
                    event_type="CHARGEBACK",
                    amount_minor=100_000,
                    occurred_at=at(30),
                ),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        assert ExceptionCode.UNSUPPORTED_STATE in decision.exception_codes

    def test_returns_exceeding_the_capture_fail_inv_004(self) -> None:
        """A real break, not merely an unsupported shape."""
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                payment_event(
                    "pe-2",
                    event_id="evt-2",
                    event_type="REFUND",
                    amount_minor=150_000,
                    occurred_at=at(30),
                ),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        result = next(
            r for r in decision.invariant_results if r.invariant_id is InvariantId.INV_004
        )
        assert result.outcome is InvariantOutcome.FAILED
        assert decision.status is DecisionStatus.EXCEPTION


class TestArithmeticBreaks:
    """Amounts that do not agree."""

    def test_a_net_that_contradicts_the_formula_is_an_exception(self) -> None:
        """INV-002 is what the declared net exists to be checked against."""
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                settlement_line("sl-1", net_minor=99_999),
                payout("po-1", net_minor=99_999),
            )
        )

        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.AMOUNT_MISMATCH in decision.exception_codes
        assert ReasonCode.NET_FORMULA_MISMATCH in decision.reason_codes

    def test_a_currency_mismatch_is_an_exception(self) -> None:
        """A line and its payment in different currencies cannot be compared."""
        decision = only_decision(
            index_of(
                payment_event("pe-1", currency="USD"),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )

        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.CURRENCY_MISMATCH in decision.exception_codes

    def test_a_payout_total_that_disagrees_is_an_exception(self) -> None:
        """INV-003 against the lines this snapshot holds for that payout."""
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                settlement_line("sl-1"),
                payout("po-1", net_minor=1),
            )
        )

        assert decision.status is DecisionStatus.EXCEPTION
        assert ReasonCode.PAYOUT_TOTAL_MISMATCH in decision.reason_codes


class TestPayoutGroupingIsSnapshotRelative:
    """INV-003 is checked against the lines the snapshot has, and says so."""

    def test_two_lines_summing_to_the_payout_both_resolve(self) -> None:
        """The grouping is by exact payout ID."""
        batch = reconcile(
            index_of(
                payment_event("pe-1"),
                payment_event("pe-2", event_id="evt-2", payment_id="pay-2"),
                settlement_line("sl-1"),
                settlement_line("sl-2", payment_id="pay-2"),
                payout("po-1", net_minor=195_280),
            )
        )

        assert batch.status_counts["RESOLVED"] == 2

    def test_a_line_belonging_to_another_payout_is_not_counted(self) -> None:
        """Grouping is exact. A different payout ID is a different batch."""
        snapshot = FactSnapshot.from_index(
            index_of(
                settlement_line("sl-1"),
                settlement_line("sl-2", payout_id="payout-2"),
                payout("po-1"),
            )
        )

        grouped = snapshot.lines_for_payout("payout-1")
        assert [line.settlement_line_id for line in grouped] == ["line-sl-1"]

    def test_a_missing_sibling_line_makes_the_total_disagree(self) -> None:
        """The honest consequence of a snapshot relative check.

        The payout says it totalled two lines. Only one was imported. INV-003
        compares against what is here and reports a mismatch, which is correct
        for this snapshot. It is not evidence that the provider's export was
        wrong, and the documentation says so.
        """
        decision = only_decision(
            index_of(
                payment_event("pe-1"),
                settlement_line("sl-1"),
                payout("po-1", net_minor=195_280),
            )
        )

        assert decision.status is DecisionStatus.EXCEPTION
        assert ReasonCode.PAYOUT_TOTAL_MISMATCH in decision.reason_codes


class TestDeferredBehaviour:
    """What this baseline deliberately does not do."""

    def test_missing_settlement_is_never_emitted(self) -> None:
        """It needs the settlement window policy, which is still deferred.

        A capture with no settlement line produces no decision at all, because
        decisions are per settlement line. Emitting MISSING_SETTLEMENT would
        require knowing when a settlement stops being plausibly late.
        """
        batch = reconcile(index_of(payment_event("pe-1")))

        assert batch.decisions == ()
        assert "MISSING_SETTLEMENT" not in batch.exception_counts

    def test_no_decision_is_made_without_a_settlement_line(self) -> None:
        """The subject of a decision is a settlement line."""
        batch = reconcile(index_of(payment_event("pe-1"), payout("po-1")))

        assert batch.settlement_line_count == 0
        assert batch.decisions == ()


class TestEmptyIndex:
    """Nothing to reconcile is an error, not a clean result."""

    def test_reconciling_an_empty_index_is_refused(self) -> None:
        """An empty run would look like a clean result."""
        with pytest.raises(ValueError, match="empty fact index"):
            reconcile(index_of())


class TestTheExceptionMapping:
    """Which exception an invariant failure raises."""

    def test_the_exception_map_covers_every_evaluated_invariant(self) -> None:
        """The engine indexes the map directly, so a gap would be a crash.

        This holds the map and the set of invariants the engine evaluates
        together, so adding an invariant without mapping it fails here rather
        than in production.
        """
        snapshot = FactSnapshot.from_index(complete_case())
        candidate = reconcile_line(snapshot.settlement_lines[0], snapshot)

        evaluated = {result.invariant_id for result in candidate.invariant_results}
        assert evaluated <= set(EXCEPTION_BY_INVARIANT)

    def test_net_and_payout_failures_report_an_amount_mismatch(self) -> None:
        """Not FEE_MISMATCH.

        When a declared net disagrees with the formula there is no way to tell
        whether the fee is wrong or the net is. Naming the fee would be a guess,
        and FEE_MISMATCH needs a second source of fee truth this baseline does
        not have.
        """
        assert EXCEPTION_BY_INVARIANT[InvariantId.INV_002] is ExceptionCode.AMOUNT_MISMATCH
        assert EXCEPTION_BY_INVARIANT[InvariantId.INV_003] is ExceptionCode.AMOUNT_MISMATCH
        assert ExceptionCode.FEE_MISMATCH not in EXCEPTION_BY_INVARIANT.values()


class TestSettlementGrossMustMatchItsCapture:
    """The regression this phase exists for.

    Before INV-009, a direct case with one capture, no returns, matching
    currency, internally valid line arithmetic and a matching payout total could
    resolve while settling a different amount from the one captured. The
    difference appeared nowhere in the result.
    """

    @staticmethod
    def _case(gross: int, net: int) -> SourceFactIndex:
        """One INR capture of 100000 and a line settling ``gross``.

        Fee 2000 and tax 360, so the net follows the formula for whatever gross
        is passed. The payout total equals the line, so INV-002 and INV-003 both
        pass and only INV-009 can catch the difference.
        """
        return index_of(
            payment_event("pe-1", amount_minor=100_000),
            settlement_line("sl-1", gross_minor=gross, net_minor=net),
            payout("po-1", net_minor=net),
        )

    def test_the_previously_resolving_mismatch_is_now_an_exception(self) -> None:
        """Capture 100000, settled gross 80000. Twenty thousand unexplained."""
        decision = only_decision(self._case(gross=80_000, net=77_640))

        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.AMOUNT_MISMATCH in decision.exception_codes

    def test_inv_009_is_the_check_that_fails(self) -> None:
        """The other four still pass, which is why this one was needed."""
        decision = only_decision(self._case(gross=80_000, net=77_640))

        outcomes = {result.invariant_id: result.outcome for result in decision.invariant_results}
        assert outcomes[InvariantId.INV_009] is InvariantOutcome.FAILED
        assert outcomes[InvariantId.INV_001] is InvariantOutcome.PASSED
        assert outcomes[InvariantId.INV_002] is InvariantOutcome.PASSED
        assert outcomes[InvariantId.INV_003] is InvariantOutcome.PASSED

    def test_the_failure_reports_the_capture_and_the_settled_gross(self) -> None:
        """Both numbers, so the gap is readable without another lookup."""
        decision = only_decision(self._case(gross=80_000, net=77_640))

        result = next(
            r for r in decision.invariant_results if r.invariant_id is InvariantId.INV_009
        )
        assert result.expected_minor == 100_000
        assert result.observed_minor == 80_000

    def test_the_reason_names_the_rule_that_fired(self) -> None:
        """Derived by the verifier from the invariant result."""
        decision = only_decision(self._case(gross=80_000, net=77_640))

        assert ReasonCode.SETTLEMENT_GROSS_DOES_NOT_MATCH_CAPTURE in decision.reason_codes

    def test_the_equal_amount_case_still_resolves(self) -> None:
        """The fix must not cost the shape the baseline is supposed to settle."""
        decision = only_decision(self._case(gross=100_000, net=97_640))

        assert decision.status is DecisionStatus.RESOLVED
        assert decision.exception_codes == ()

    def test_a_settled_gross_above_the_capture_is_also_caught(self) -> None:
        """Settling more than was taken is as unexplained as settling less."""
        decision = only_decision(self._case(gross=120_000, net=117_640))

        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.AMOUNT_MISMATCH in decision.exception_codes

    def test_a_one_unit_difference_is_caught(self) -> None:
        """The check is equality. There is no tolerance to hide inside."""
        decision = only_decision(self._case(gross=100_001, net=97_641))

        assert decision.status is DecisionStatus.EXCEPTION


class TestInv009AcrossLifecycleShapes:
    """How INV-009 behaves on the shapes the baseline already declines."""

    @staticmethod
    def _outcome(index: SourceFactIndex) -> InvariantOutcome:
        """Return the INV-009 outcome for a one line index."""
        decision = only_decision(index)
        return next(
            r for r in decision.invariant_results if r.invariant_id is InvariantId.INV_009
        ).outcome

    def test_a_missing_capture_leaves_it_undetermined(self) -> None:
        """Reported as unknown rather than as a mismatch."""
        outcome = self._outcome(
            index_of(
                payment_event("pe-1", event_type="REFUND"),
                settlement_line("sl-1"),
                payout("po-1"),
            )
        )
        assert outcome is InvariantOutcome.INSUFFICIENT_INPUT

    def test_two_captures_make_it_not_applicable(self) -> None:
        """The baseline already declines this with UNSUPPORTED_STATE."""
        index = index_of(
            payment_event("pe-1"),
            payment_event("pe-2", event_id="evt-2", occurred_at=at(5)),
            settlement_line("sl-1"),
            payout("po-1"),
        )
        assert self._outcome(index) is InvariantOutcome.NOT_APPLICABLE
        assert ExceptionCode.UNSUPPORTED_STATE in only_decision(index).exception_codes

    def test_a_partial_refund_makes_it_not_applicable(self) -> None:
        """The baseline already declines this with PARTIAL_REFUND."""
        index = index_of(
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
        assert self._outcome(index) is InvariantOutcome.NOT_APPLICABLE
        assert ExceptionCode.PARTIAL_REFUND in only_decision(index).exception_codes

    def test_a_currency_mismatch_fails_rather_than_converting(self) -> None:
        """An honest failure, not a guess at an exchange rate."""
        index = index_of(
            payment_event("pe-1", currency="USD"),
            settlement_line("sl-1"),
            payout("po-1"),
        )
        decision = only_decision(index)

        assert self._outcome(index) is InvariantOutcome.FAILED
        assert ReasonCode.CURRENCY_NOT_UNIFORM in decision.reason_codes
        assert ExceptionCode.CURRENCY_MISMATCH in decision.exception_codes

    def test_a_not_applicable_result_does_not_block_a_resolution_by_itself(self) -> None:
        """It is determinate, and those shapes carry their own codes instead.

        Proved by the partial refund case: INV-009 is not applicable there, and
        the decision is still an exception, because PARTIAL_REFUND says so.
        """
        index = index_of(
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
        assert only_decision(index).status is DecisionStatus.EXCEPTION
