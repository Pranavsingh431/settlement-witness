"""Tests for the invariant catalogue and its checks.

Every invariant in the catalogue is exercised here, including the case where
information is missing. That case matters most: an invariant that reports a
break when it simply could not tell would send a finance team chasing a
difference that does not exist.
"""

import pytest
from pydantic import ValidationError

from app.domain.codes import ReasonCode
from app.domain.invariants import (
    INVARIANT_CATALOGUE,
    REQUIRED_FOR_RESOLUTION,
    InvariantId,
    InvariantOutcome,
    InvariantResult,
    MissingInputPolicy,
    check_append_only,
    check_idempotency,
    check_money_compatibility,
    check_payout_total,
    check_returns_within_capture,
    check_settlement_line_net,
)
from app.domain.lifecycle import PaymentEventType
from tests.domain.conftest import (
    make_breakdown,
    make_event,
    make_fact,
    make_money,
    make_payout,
    make_settlement_line,
)


class TestCatalogue:
    """The catalogue is data, so it can be checked like data."""

    def test_all_eight_invariants_are_registered(self) -> None:
        """INV-001 through INV-008 all exist."""
        assert {invariant_id.value for invariant_id in INVARIANT_CATALOGUE} == {
            f"INV-00{number}" for number in range(1, 9)
        }

    def test_every_entry_is_keyed_by_its_own_id(self) -> None:
        """A copy and paste slip in the catalogue would be caught here."""
        for invariant_id, spec in INVARIANT_CATALOGUE.items():
            assert spec.invariant_id is invariant_id

    def test_every_entry_declares_a_missing_input_policy(self) -> None:
        """Silence about missing data is what the contract forbids."""
        for spec in INVARIANT_CATALOGUE.values():
            assert isinstance(spec.missing_input_policy, MissingInputPolicy)

    def test_the_catalogue_cannot_be_modified(self) -> None:
        """No caller may quietly add or drop an invariant at runtime."""
        with pytest.raises(TypeError):
            INVARIANT_CATALOGUE[InvariantId.INV_001] = None  # type: ignore[index]

    def test_required_set_matches_the_catalogue_flags(self) -> None:
        """The required set is derived, so the two cannot drift apart."""
        assert {
            invariant_id
            for invariant_id, spec in INVARIANT_CATALOGUE.items()
            if spec.required_for_resolution
        } == REQUIRED_FOR_RESOLUTION

    def test_decision_level_invariants_are_not_required_of_a_decision(self) -> None:
        """Requiring a decision to prove its own correctness would be circular."""
        assert InvariantId.INV_006 not in REQUIRED_FOR_RESOLUTION
        assert InvariantId.INV_007 not in REQUIRED_FOR_RESOLUTION

    def test_ingestion_invariants_are_not_required_of_a_decision(self) -> None:
        """INV-005 and INV-008 are checked long before a decision exists."""
        assert InvariantId.INV_005 not in REQUIRED_FOR_RESOLUTION
        assert InvariantId.INV_008 not in REQUIRED_FOR_RESOLUTION


class TestInvariantResult:
    """A result carries codes and numbers, never prose."""

    def test_a_failure_must_name_its_reason(self) -> None:
        """A failure with no reason code cannot be acted on."""
        with pytest.raises(ValidationError, match="must carry a reason_code"):
            InvariantResult(invariant_id=InvariantId.INV_002, outcome=InvariantOutcome.FAILED)

    def test_passed_and_not_applicable_are_determinate(self) -> None:
        """Both are real answers, so neither blocks a resolution."""
        for outcome in (InvariantOutcome.PASSED, InvariantOutcome.NOT_APPLICABLE):
            result = InvariantResult(invariant_id=InvariantId.INV_004, outcome=outcome)
            assert result.is_determinate

    def test_failed_and_insufficient_input_are_not_determinate(self) -> None:
        """Neither may be treated as a pass."""
        failed = InvariantResult(
            invariant_id=InvariantId.INV_002,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.NET_FORMULA_MISMATCH,
        )
        unknown = InvariantResult(
            invariant_id=InvariantId.INV_002, outcome=InvariantOutcome.INSUFFICIENT_INPUT
        )
        assert not failed.is_determinate
        assert not unknown.is_determinate

    def test_a_result_cannot_carry_free_text(self) -> None:
        """There is no field through which model prose could arrive."""
        with pytest.raises(ValidationError):
            InvariantResult(
                invariant_id=InvariantId.INV_002,
                outcome=InvariantOutcome.PASSED,
                note="the model is confident about this one",  # type: ignore[call-arg]
            )


class TestInv001MoneyCompatibility:
    """INV-001: amounts compared together share a currency."""

    def test_one_currency_passes(self) -> None:
        """The normal case."""
        result = check_money_compatibility([make_money(1), make_money(2)])
        assert result.outcome is InvariantOutcome.PASSED

    def test_two_currencies_fail(self) -> None:
        """Mixed currencies cannot be compared."""
        result = check_money_compatibility([make_money(1, "INR"), make_money(1, "USD")])
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.CURRENCY_NOT_UNIFORM

    def test_nothing_supplied_is_insufficient_input_not_a_pass(self) -> None:
        """Claiming an empty set is consistent would be a free pass."""
        result = check_money_compatibility([])
        assert result.outcome is InvariantOutcome.INSUFFICIENT_INPUT


class TestInv002SettlementLineNet:
    """INV-002: the declared net follows the signed formula."""

    def test_a_consistent_line_passes(self) -> None:
        """10000 - 200 - 36 + 0 is 9764."""
        assert check_settlement_line_net(make_settlement_line()).outcome is InvariantOutcome.PASSED

    def test_a_line_that_contradicts_the_formula_fails(self) -> None:
        """The declared net is what is being checked."""
        result = check_settlement_line_net(make_settlement_line(net_minor=9_800))
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.NET_FORMULA_MISMATCH

    def test_the_failure_reports_both_numbers(self) -> None:
        """A person should not have to recompute the expected value."""
        result = check_settlement_line_net(make_settlement_line(net_minor=9_800))
        assert result.expected_minor == 9_764
        assert result.observed_minor == 9_800

    def test_a_signed_adjustment_is_honoured(self) -> None:
        """Adjustment is added, so the expected net moves with it."""
        line = make_settlement_line(breakdown=make_breakdown(adjustment_minor=-64), net_minor=9_700)
        assert check_settlement_line_net(line).outcome is InvariantOutcome.PASSED


class TestInv003PayoutTotal:
    """INV-003: a payout total equals the sum of the nets of its own lines."""

    def test_a_matching_batch_passes(self) -> None:
        """One line of 9764 in a batch of 9764."""
        result = check_payout_total(make_payout(), [make_settlement_line()])
        assert result.outcome is InvariantOutcome.PASSED

    def test_many_lines_sum_correctly(self) -> None:
        """A payout is normally a batch."""
        lines = [
            make_settlement_line(settlement_line_id="sl-1"),
            make_settlement_line(settlement_line_id="sl-2"),
        ]
        payout = make_payout(settlement_line_ids=("sl-1", "sl-2"), net_minor=19_528)
        assert check_payout_total(payout, lines).outcome is InvariantOutcome.PASSED

    def test_a_wrong_total_fails_with_both_numbers(self) -> None:
        """The break is real and both sides are known."""
        result = check_payout_total(make_payout(net_minor=9_000), [make_settlement_line()])
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.PAYOUT_TOTAL_MISMATCH
        assert result.expected_minor == 9_764
        assert result.observed_minor == 9_000

    def test_a_partial_set_of_lines_is_insufficient_input(self) -> None:
        """A sum over some of the lines says nothing about the total.

        Reporting this as a mismatch would manufacture a break for every payout
        that is still being assembled.
        """
        payout = make_payout(settlement_line_ids=("sl-1", "sl-2"), net_minor=19_528)
        result = check_payout_total(payout, [make_settlement_line(settlement_line_id="sl-1")])
        assert result.outcome is InvariantOutcome.INSUFFICIENT_INPUT

    def test_an_extra_line_is_also_insufficient_input(self) -> None:
        """A line the payout does not claim makes the sum meaningless too."""
        lines = [
            make_settlement_line(settlement_line_id="sl-1"),
            make_settlement_line(settlement_line_id="sl-2"),
        ]
        assert (
            check_payout_total(make_payout(), lines).outcome is InvariantOutcome.INSUFFICIENT_INPUT
        )

    def test_a_mixed_currency_batch_fails(self) -> None:
        """A total across currencies has no meaning."""
        line = make_settlement_line(breakdown=make_breakdown(currency="USD"))
        result = check_payout_total(make_payout(currency="INR"), [line])
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.CURRENCY_NOT_UNIFORM

    def test_missing_input_policy_is_pending(self) -> None:
        """A payout still filling up is waiting, not broken."""
        spec = INVARIANT_CATALOGUE[InvariantId.INV_003]
        assert spec.missing_input_policy is MissingInputPolicy.PENDING


class TestInv004ReturnsWithinCapture:
    """INV-004: refunds, reversals and chargebacks stay within the capture."""

    def test_a_refund_below_the_capture_passes(self) -> None:
        """The normal partial refund."""
        events = [
            make_event(amount=make_money(10_000)),
            make_event(
                event_id="evt-2", event_type=PaymentEventType.REFUND, amount=make_money(4_000)
            ),
        ]
        assert check_returns_within_capture(events).outcome is InvariantOutcome.PASSED

    def test_returns_above_the_capture_fail(self) -> None:
        """More money out than came in is a real break."""
        events = [
            make_event(amount=make_money(10_000)),
            make_event(
                event_id="evt-2", event_type=PaymentEventType.REFUND, amount=make_money(12_000)
            ),
        ]
        result = check_returns_within_capture(events)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.RETURNS_EXCEED_CAPTURE
        assert result.expected_minor == 10_000
        assert result.observed_minor == 12_000

    def test_all_three_returning_types_count_against_the_capture(self) -> None:
        """A chargeback drains the same pot as a refund."""
        events = [
            make_event(amount=make_money(10_000)),
            make_event(
                event_id="evt-2", event_type=PaymentEventType.REFUND, amount=make_money(4_000)
            ),
            make_event(
                event_id="evt-3", event_type=PaymentEventType.REVERSAL, amount=make_money(4_000)
            ),
            make_event(
                event_id="evt-4", event_type=PaymentEventType.CHARGEBACK, amount=make_money(4_000)
            ),
        ]
        assert check_returns_within_capture(events).outcome is InvariantOutcome.FAILED

    def test_no_capture_is_insufficient_input(self) -> None:
        """Without the capture the ceiling is unknown, so nothing can be said."""
        events = [
            make_event(event_type=PaymentEventType.REFUND, amount=make_money(4_000)),
        ]
        assert check_returns_within_capture(events).outcome is InvariantOutcome.INSUFFICIENT_INPUT

    def test_no_returns_is_not_applicable_rather_than_passed(self) -> None:
        """A determinate answer that does not block a resolution."""
        result = check_returns_within_capture([make_event()])
        assert result.outcome is InvariantOutcome.NOT_APPLICABLE
        assert result.reason_code is ReasonCode.NO_APPLICABLE_RETURN_EVENTS
        assert result.is_determinate

    def test_mixed_currencies_fail(self) -> None:
        """Comparing a USD refund against an INR capture is meaningless."""
        events = [
            make_event(amount=make_money(10_000, "INR")),
            make_event(
                event_id="evt-2",
                event_type=PaymentEventType.REFUND,
                amount=make_money(10, "USD"),
            ),
        ]
        result = check_returns_within_capture(events)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.CURRENCY_NOT_UNIFORM

    def test_an_empty_event_list_is_insufficient_input(self) -> None:
        """Nothing supplied means nothing known."""
        assert check_returns_within_capture([]).outcome is InvariantOutcome.INSUFFICIENT_INPUT


class TestInv005Idempotency:
    """INV-005: one idempotency identity carries one payload."""

    def test_a_first_sighting_passes(self) -> None:
        """Nothing to conflict with."""
        assert check_idempotency(make_fact(), None).outcome is InvariantOutcome.PASSED

    def test_an_identical_replay_passes(self) -> None:
        """Duplicate delivery is expected behaviour, not a break."""
        assert check_idempotency(make_fact(), make_fact()).outcome is InvariantOutcome.PASSED

    def test_a_contradicting_payload_fails(self) -> None:
        """The same identity cannot describe two different things."""
        stored = make_fact(payload={"amount_minor": 9_764})
        changed = make_fact(payload={"amount_minor": 1})
        result = check_idempotency(changed, stored)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.PAYLOAD_HASH_CONFLICT

    def test_missing_input_policy_is_exception(self) -> None:
        """A conflict is the problem itself, so it is raised."""
        spec = INVARIANT_CATALOGUE[InvariantId.INV_005]
        assert spec.missing_input_policy is MissingInputPolicy.EXCEPTION


class TestInv008AppendOnly:
    """INV-008: a stored source fact is never rewritten."""

    def test_an_identical_fact_passes(self) -> None:
        """Re-presenting the same fact changes nothing."""
        assert check_append_only(make_fact(), make_fact()).outcome is InvariantOutcome.PASSED

    def test_a_changed_fact_with_the_same_record_id_fails(self) -> None:
        """A correction is a new fact, never an edit to an old one."""
        result = check_append_only(
            make_fact(payload={"amount_minor": 9_764}),
            make_fact(payload={"amount_minor": 1}),
        )
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.SOURCE_FACT_REWRITE_ATTEMPTED

    def test_a_change_to_a_non_payload_field_also_fails(self) -> None:
        """Append-only covers the whole record, not just the payload."""
        result = check_append_only(make_fact(), make_fact(provider_event_id="evt-changed"))
        assert result.outcome is InvariantOutcome.FAILED

    def test_two_different_records_are_not_applicable(self) -> None:
        """This invariant only speaks about one record ID at a time."""
        result = check_append_only(make_fact(), make_fact(source_record_id="rec-2"))
        assert result.outcome is InvariantOutcome.NOT_APPLICABLE
