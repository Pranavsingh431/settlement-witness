"""Building one scenario of each shape, and declaring what it must produce.

Every anomaly is built by taking its control and applying exactly one change.
That is what makes a paired control meaningful: if two cases differ in three
ways and only one resolves, the difference has not been isolated.

The expectations below are reasoned from the contract, not read off a run. Each
carries a one line rationale so a person can check the oracle without executing
anything.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.benchmark.specs import (
    SYNTHETIC_MERCHANT_PREFIX,
    EventSpec,
    LineSpec,
    OracleExpectation,
    PayoutSpec,
    ScenarioSpec,
    TemplateId,
)
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus
from app.domain.lifecycle import PaymentEventType

EPOCH = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
"""The instant every scenario is offset from. Fixed, so a seed alone decides the
corpus and the calendar does not."""

FEE_BASIS_POINTS = 200
"""Two percent, applied to gross."""

TAX_BASIS_POINTS = 1800
"""Eighteen percent, applied to the fee."""


def _at(minutes: int) -> str:
    """Return an ISO timestamp offset from the epoch."""
    return (EPOCH + timedelta(minutes=minutes)).isoformat()


def fee_for(gross_minor: int) -> int:
    """Return the fee for a gross amount, in whole minor units."""
    return gross_minor * FEE_BASIS_POINTS // 10_000


def tax_for(fee_minor: int) -> int:
    """Return the tax on a fee, in whole minor units."""
    return fee_minor * TAX_BASIS_POINTS // 10_000


class ScenarioBuilder:
    """Builds one scenario, given its identity and its amounts.

    Every method returns a complete `ScenarioSpec`. The control builder is the
    base, and each anomaly builder calls it and changes one thing, so the
    difference between a pair is visible in this file rather than buried in
    generated output.
    """

    def __init__(self, scenario_id: str, gross_minor: int, currency: str = "INR") -> None:
        """Create a builder for one scenario.

        Args:
            scenario_id: Opaque identity, used to namespace every record.
            gross_minor: The captured amount, in minor units.
            currency: The currency of the case.
        """
        self.scenario_id = scenario_id
        self.gross_minor = gross_minor
        self.currency = currency
        self.fee_minor = fee_for(gross_minor)
        self.tax_minor = tax_for(self.fee_minor)
        self.net_minor = gross_minor - self.fee_minor - self.tax_minor

    @property
    def merchant_id(self) -> str:
        """Return the merchant, marked synthetic."""
        return f"{SYNTHETIC_MERCHANT_PREFIX}-{self.scenario_id}"

    def capture(self, **overrides: object) -> EventSpec:
        """Return the capture event for this scenario."""
        fields: dict[str, object] = {
            "provider_event_id": f"pe-{self.scenario_id}-1",
            "event_id": f"evt-{self.scenario_id}-1",
            "payment_id": f"pay-{self.scenario_id}",
            "merchant_id": self.merchant_id,
            "event_type": PaymentEventType.CAPTURE,
            "amount_minor": self.gross_minor,
            "currency": self.currency,
            "occurred_at": _at(0),
        }
        fields.update(overrides)
        return EventSpec.model_validate(fields)

    def line(self, **overrides: object) -> LineSpec:
        """Return the settlement line for this scenario."""
        fields: dict[str, object] = {
            "provider_event_id": f"sl-{self.scenario_id}-1",
            "settlement_line_id": f"line-{self.scenario_id}",
            "payout_id": f"payout-{self.scenario_id}",
            "payment_id": f"pay-{self.scenario_id}",
            "gross_minor": self.gross_minor,
            "fee_minor": self.fee_minor,
            "tax_minor": self.tax_minor,
            "adjustment_minor": 0,
            "net_minor": self.net_minor,
            "currency": self.currency,
            "occurred_at": _at(1440),
        }
        fields.update(overrides)
        return LineSpec.model_validate(fields)

    def payout(self, **overrides: object) -> PayoutSpec:
        """Return the payout for this scenario."""
        fields: dict[str, object] = {
            "provider_event_id": f"po-{self.scenario_id}-1",
            "payout_id": f"payout-{self.scenario_id}",
            "merchant_id": self.merchant_id,
            "net_minor": self.net_minor,
            "currency": self.currency,
            "utr": f"SYNTHUTR{self.scenario_id.replace('-', '')}",
            "occurred_at": _at(1500),
        }
        fields.update(overrides)
        return PayoutSpec.model_validate(fields)

    def _spec(
        self,
        template: TemplateId,
        expected: OracleExpectation,
        *,
        paired_control_id: str | None = None,
        events: tuple[EventSpec, ...] | None = None,
        lines: tuple[LineSpec, ...] | None = None,
        payouts: tuple[PayoutSpec, ...] | None = None,
    ) -> ScenarioSpec:
        """Assemble a scenario from its parts, defaulting to the control shape.

        The payout total is derived from the settlement lines unless a template
        overrides it. That derivation is what makes a paired control meaningful:
        breaking a line's declared net is then one edit, and the payout following
        it is a consequence rather than a second independent change. Only
        PAYOUT_TOTAL_MISMATCH overrides the payout, because breaking that
        derivation is the whole point of that template.
        """
        settlement_lines = lines if lines is not None else (self.line(),)
        derived_total = sum(line.net_minor for line in settlement_lines)
        return ScenarioSpec(
            scenario_id=self.scenario_id,
            template=template,
            paired_control_id=paired_control_id,
            payment_events=events if events is not None else (self.capture(),),
            settlement_lines=settlement_lines,
            payouts=payouts if payouts is not None else (self.payout(net_minor=derived_total),),
            expected=expected,
        )

    def resolved_direct(self) -> ScenarioSpec:
        """One capture, one line settling it exactly, one matching payout."""
        return self._spec(
            TemplateId.RESOLVED_DIRECT,
            OracleExpectation(
                status=DecisionStatus.RESOLVED,
                exception_codes=(),
                rationale=(
                    "One capture, nothing returned, gross equals the capture, the "
                    "net follows the formula and the payout equals the line, so "
                    "every required invariant is determinate and passing."
                ),
            ),
        )

    def net_formula_mismatch(self, control_id: str) -> ScenarioSpec:
        """The declared net is one unit away from the formula."""
        return self._spec(
            TemplateId.NET_FORMULA_MISMATCH,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
                rationale=(
                    "INV-002 fails because the declared net is not gross minus fee "
                    "minus tax plus adjustment."
                ),
            ),
            paired_control_id=control_id,
            lines=(self.line(net_minor=self.net_minor + 1),),
        )

    def capture_gross_mismatch(self, control_id: str) -> ScenarioSpec:
        """The capture is larger than the gross the line settles."""
        return self._spec(
            TemplateId.CAPTURE_GROSS_MISMATCH,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
                rationale=(
                    "INV-009 fails because the settled gross is not the amount the "
                    "capture took, which is an unexplained monetary difference."
                ),
            ),
            paired_control_id=control_id,
            events=(self.capture(amount_minor=self.gross_minor + 10_000),),
        )

    def payout_total_mismatch(self, control_id: str) -> ScenarioSpec:
        """The payout total is one unit away from the line it covers."""
        return self._spec(
            TemplateId.PAYOUT_TOTAL_MISMATCH,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
                rationale=(
                    "INV-003 fails because the payout total is not the sum of the "
                    "settlement lines evidenced for that payout in this snapshot."
                ),
            ),
            paired_control_id=control_id,
            payouts=(self.payout(net_minor=self.net_minor + 1),),
        )

    def missing_payment(self, control_id: str) -> ScenarioSpec:
        """The line names a payment, and no payment document describes it."""
        return self._spec(
            TemplateId.MISSING_PAYMENT,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.MISSING_PAYMENT,),
                rationale=(
                    "No source fact describes the payment the line names, so the "
                    "baseline raises MISSING_PAYMENT, which implies EXCEPTION."
                ),
            ),
            paired_control_id=control_id,
            events=(),
        )

    def missing_payout(self, control_id: str) -> ScenarioSpec:
        """The line names a payout, and no payout document describes it."""
        return self._spec(
            TemplateId.MISSING_PAYOUT,
            OracleExpectation(
                status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                exception_codes=(ExceptionCode.INSUFFICIENT_EVIDENCE,),
                rationale=(
                    "Without the payout there is nothing to check the batch total "
                    "against, so the case is reported as insufficient evidence "
                    "rather than as a mismatch."
                ),
            ),
            paired_control_id=control_id,
            payouts=(),
        )

    def currency_mismatch(self, control_id: str) -> ScenarioSpec:
        """The capture is in a different currency from the line."""
        return self._spec(
            TemplateId.CURRENCY_MISMATCH,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(
                    ExceptionCode.AMOUNT_MISMATCH,
                    ExceptionCode.CURRENCY_MISMATCH,
                ),
                rationale=(
                    "INV-001 fails because the amounts are not in one currency, and "
                    "INV-009 fails for the same reason rather than converting."
                ),
            ),
            paired_control_id=control_id,
            events=(self.capture(currency="USD"),),
        )

    def partial_refund(self, control_id: str) -> ScenarioSpec:
        """Part of the capture is refunded, after the capture."""
        refunded = self.gross_minor // 4
        return self._spec(
            TemplateId.PARTIAL_REFUND,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.PARTIAL_REFUND,),
                rationale=(
                    "A balance remains after the refund and the baseline has no rule "
                    "for how a partially refunded payment settles, so it declines."
                ),
            ),
            paired_control_id=control_id,
            events=(
                self.capture(),
                self.capture(
                    provider_event_id=f"pe-{self.scenario_id}-2",
                    event_id=f"evt-{self.scenario_id}-2",
                    event_type=PaymentEventType.REFUND,
                    amount_minor=refunded,
                    occurred_at=_at(720),
                ),
            ),
        )

    def out_of_order_return(self, control_id: str) -> ScenarioSpec:
        """A partial refund dated before the capture it refunds."""
        refunded = self.gross_minor // 4
        return self._spec(
            TemplateId.OUT_OF_ORDER_RETURN,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(
                    ExceptionCode.OUT_OF_ORDER_EVENT,
                    ExceptionCode.PARTIAL_REFUND,
                ),
                rationale=(
                    "The sequence is impossible as reported, and the refund is also "
                    "partial, so both findings are raised and the stronger decides."
                ),
            ),
            paired_control_id=control_id,
            events=(
                self.capture(),
                self.capture(
                    provider_event_id=f"pe-{self.scenario_id}-2",
                    event_id=f"evt-{self.scenario_id}-2",
                    event_type=PaymentEventType.REFUND,
                    amount_minor=refunded,
                    occurred_at=_at(-720),
                ),
            ),
        )

    def multiple_captures(self, control_id: str) -> ScenarioSpec:
        """Two captures for one payment."""
        return self._spec(
            TemplateId.MULTIPLE_CAPTURES,
            OracleExpectation(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.UNSUPPORTED_STATE,),
                rationale=(
                    "Choosing which capture the line settled would be a guess, so "
                    "the baseline reports the state as unsupported instead."
                ),
            ),
            paired_control_id=control_id,
            events=(
                self.capture(),
                self.capture(
                    provider_event_id=f"pe-{self.scenario_id}-2",
                    event_id=f"evt-{self.scenario_id}-2",
                    occurred_at=_at(30),
                ),
            ),
        )


#: How to build each anomaly from a builder and the identity of its control.
ANOMALY_BUILDERS: dict[TemplateId, Callable[[ScenarioBuilder, str], ScenarioSpec]] = {
    TemplateId.NET_FORMULA_MISMATCH: ScenarioBuilder.net_formula_mismatch,
    TemplateId.CAPTURE_GROSS_MISMATCH: ScenarioBuilder.capture_gross_mismatch,
    TemplateId.PAYOUT_TOTAL_MISMATCH: ScenarioBuilder.payout_total_mismatch,
    TemplateId.MISSING_PAYMENT: ScenarioBuilder.missing_payment,
    TemplateId.MISSING_PAYOUT: ScenarioBuilder.missing_payout,
    TemplateId.CURRENCY_MISMATCH: ScenarioBuilder.currency_mismatch,
    TemplateId.PARTIAL_REFUND: ScenarioBuilder.partial_refund,
    TemplateId.OUT_OF_ORDER_RETURN: ScenarioBuilder.out_of_order_return,
    TemplateId.MULTIPLE_CAPTURES: ScenarioBuilder.multiple_captures,
}
