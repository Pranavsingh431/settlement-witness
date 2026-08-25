"""What a scenario is, before it becomes CSV rows.

A scenario is a structured description of one reconciliation case: the payment
events, the settlement line, the payout, and what the contract says the outcome
must be. Everything downstream is derived from it.

The expectation is part of the specification, not something measured later. That
is the point of the whole harness: if the expected outcome were obtained by
running the baseline, the evaluation would only ever confirm that the baseline
agrees with itself.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus
from app.domain.lifecycle import PaymentEventType

SYNTHETIC_MERCHANT_PREFIX = "SYNTH-MERCHANT"
"""Every generated merchant carries this. Synthetic data must be obviously so.

If one of these documents is ever seen outside the harness, the identifiers say
what it is without anyone having to check where it came from.
"""


class TemplateId(StrEnum):
    """The reconciliation shapes this corpus covers.

    One control that must resolve, and nine anomalies that must not. Each
    anomaly names the single causal difference from its control.
    """

    RESOLVED_DIRECT = "RESOLVED_DIRECT"
    """One capture, one line settling it, one payout. Nothing wrong."""

    NET_FORMULA_MISMATCH = "NET_FORMULA_MISMATCH"
    """The declared net does not follow gross minus fee minus tax."""

    CAPTURE_GROSS_MISMATCH = "CAPTURE_GROSS_MISMATCH"
    """The line settles a different gross from the capture it names."""

    PAYOUT_TOTAL_MISMATCH = "PAYOUT_TOTAL_MISMATCH"
    """The payout total is not the sum of the lines evidenced for it."""

    MISSING_PAYMENT = "MISSING_PAYMENT"
    """The line names a payment no document describes."""

    MISSING_PAYOUT = "MISSING_PAYOUT"
    """The line names a payout no document describes."""

    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    """The capture and the line are in different currencies."""

    PARTIAL_REFUND = "PARTIAL_REFUND"
    """Part of the capture was refunded, so a balance remains."""

    OUT_OF_ORDER_RETURN = "OUT_OF_ORDER_RETURN"
    """A refund dated before the capture it refunds."""

    MULTIPLE_CAPTURES = "MULTIPLE_CAPTURES"
    """Two captures for one payment, so which the line settled is undecidable."""


ANOMALY_TEMPLATES: tuple[TemplateId, ...] = tuple(
    template for template in TemplateId if template is not TemplateId.RESOLVED_DIRECT
)
"""Every template that must not resolve. Each one needs a paired control."""


class EventSpec(BaseModel):
    """One payment event row, before rendering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_event_id: str
    event_id: str
    payment_id: str
    merchant_id: str
    event_type: PaymentEventType
    amount_minor: int
    currency: str
    occurred_at: str


class LineSpec(BaseModel):
    """One settlement line row, before rendering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_event_id: str
    settlement_line_id: str
    payout_id: str
    payment_id: str
    gross_minor: int
    fee_minor: int
    tax_minor: int
    adjustment_minor: int
    net_minor: int
    currency: str
    occurred_at: str


class PayoutSpec(BaseModel):
    """One payout row, before rendering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_event_id: str
    payout_id: str
    merchant_id: str
    net_minor: int
    currency: str
    utr: str
    occurred_at: str


class OracleExpectation(BaseModel):
    """What the contract says the outcome must be.

    Declared from the scenario, never obtained by running the baseline. Evidence
    record IDs are filled in once the documents are rendered, because a record ID
    depends on the document hash, but which records are expected is decided here
    from the structure of the case.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DecisionStatus
    exception_codes: tuple[ExceptionCode, ...]
    rationale: str
    """Why the contract requires this outcome, in one sentence, for a reader
    checking the oracle by hand."""


class ScenarioSpec(BaseModel):
    """One complete case, with its expected outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    """Opaque. It carries no hint of the template, because these identifiers
    reach the CSV documents and the system under test must not be able to read
    the answer off its own input."""

    template: TemplateId
    paired_control_id: str | None
    """The control this anomaly is matched to, or None for a control itself."""

    payment_events: tuple[EventSpec, ...]
    settlement_lines: tuple[LineSpec, ...]
    payouts: tuple[PayoutSpec, ...]
    expected: OracleExpectation

    @property
    def subject_settlement_line_id(self) -> str:
        """Return the line the decision for this scenario is about."""
        return self.settlement_lines[0].settlement_line_id

    @property
    def is_control(self) -> bool:
        """Return True when this scenario is expected to resolve."""
        return self.template is TemplateId.RESOLVED_DIRECT
