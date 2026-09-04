"""Turn an immutable reconciliation decision into a functional close plan.

The baseline answers whether a settlement line is supported. A finance
operator needs the next question too: what should happen now, and what evidence
would make the next run safe to resolve? This module answers that question
without editing the decision and without asking a model to invent an action.

The plan is a counterfactual over the contract, not over the numbers. It never
says "change an amount until the status turns green". It names an authoritative
record or investigation, the team that owns it, and the proof the verifier must
observe in a new run. That constraint keeps the proposed recourse inside the
same process that produced the certificate.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.codes import ExceptionCode, precedence_rank
from app.domain.decisions import DecisionStatus, ReconciliationDecision

CLOSURE_PLAN_VERSION = "1.0.0"

RESOLUTION_GATE = (
    "Import authoritative evidence and create a new reconciliation run. Close only when that "
    "new decision is RESOLVED, every citation verifies, and every required invariant holds."
)


class ClosureDisposition(StrEnum):
    """The kind of work the line needs next; never a decision status."""

    NO_ACTION = "NO_ACTION"
    COLLECT_EVIDENCE = "COLLECT_EVIDENCE"
    INVESTIGATE_SOURCE = "INVESTIGATE_SOURCE"
    MONITOR = "MONITOR"
    ESCALATE = "ESCALATE"


class ClosureLane(StrEnum):
    """The operational queue that should own the next action."""

    NONE = "NONE"
    EVIDENCE_OPERATIONS = "EVIDENCE_OPERATIONS"
    PSP_OPERATIONS = "PSP_OPERATIONS"
    DATA_QUALITY = "DATA_QUALITY"
    FINANCE_CONTROL = "FINANCE_CONTROL"


class ClosureAction(BaseModel):
    """One bounded action and the proof that would make it complete."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_code: str
    owner_lane: ClosureLane
    title: str
    instruction: str
    evidence_required: str
    supported_by_current_contract: bool
    """Whether today's importer and baseline can verify the requested proof.

    False is a capability gap, not permission to close manually. It routes the
    line to finance control while keeping the baseline conclusion unchanged.
    """


class ClosurePlan(BaseModel):
    """A deterministic route from a decision to its next safe action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_version: str = CLOSURE_PLAN_VERSION
    baseline_status: DecisionStatus
    disposition: ClosureDisposition
    primary_owner: ClosureLane
    headline: str
    blocking_codes: tuple[str, ...]
    actions: tuple[ClosureAction, ...]
    requires_new_run: bool
    resolution_gate: str

    @model_validator(mode="after")
    def _status_and_work_agree(self) -> "ClosurePlan":
        resolved = self.baseline_status is DecisionStatus.RESOLVED
        if resolved and (
            self.disposition is not ClosureDisposition.NO_ACTION
            or self.primary_owner is not ClosureLane.NONE
            or self.blocking_codes
            or self.actions
            or self.requires_new_run
        ):
            raise ValueError("a resolved decision cannot carry closure work")
        if not resolved and (
            self.disposition is ClosureDisposition.NO_ACTION
            or self.primary_owner is ClosureLane.NONE
            or not self.blocking_codes
            or not self.actions
            or not self.requires_new_run
        ):
            raise ValueError("an unresolved decision must carry bounded closure work")
        return self


def _action(
    code: ExceptionCode,
    lane: ClosureLane,
    title: str,
    instruction: str,
    evidence: str,
    *,
    supported: bool,
) -> ClosureAction:
    return ClosureAction(
        action_code=code.value,
        owner_lane=lane,
        title=title,
        instruction=instruction,
        evidence_required=evidence,
        supported_by_current_contract=supported,
    )


_PLAYBOOKS: dict[ExceptionCode, tuple[ClosureDisposition, ClosureAction]] = {
    ExceptionCode.MISSING_PAYMENT: (
        ClosureDisposition.COLLECT_EVIDENCE,
        _action(
            ExceptionCode.MISSING_PAYMENT,
            ClosureLane.EVIDENCE_OPERATIONS,
            "Fetch the payment lifecycle record",
            "Request the provider payment-event export referenced by this settlement line.",
            "At least one valid PAYMENT_EVENT linked by the exact payment ID.",
            supported=True,
        ),
    ),
    ExceptionCode.MISSING_SETTLEMENT: (
        ClosureDisposition.COLLECT_EVIDENCE,
        _action(
            ExceptionCode.MISSING_SETTLEMENT,
            ClosureLane.EVIDENCE_OPERATIONS,
            "Fetch the missing settlement record",
            "Request the provider settlement export for the payment and expected "
            "settlement window.",
            "A valid SETTLEMENT_LINE linked to the captured payment.",
            supported=True,
        ),
    ),
    ExceptionCode.AMOUNT_MISMATCH: (
        ClosureDisposition.INVESTIGATE_SOURCE,
        _action(
            ExceptionCode.AMOUNT_MISMATCH,
            ClosureLane.PSP_OPERATIONS,
            "Trace the amount break",
            "Compare capture gross, settlement gross, deductions, and payout total at the source.",
            "An authoritative correction or adjustment record that makes every amount "
            "invariant hold.",
            supported=False,
        ),
    ),
    ExceptionCode.FEE_MISMATCH: (
        ClosureDisposition.INVESTIGATE_SOURCE,
        _action(
            ExceptionCode.FEE_MISMATCH,
            ClosureLane.FINANCE_CONTROL,
            "Reconcile the fee basis",
            "Compare fee and tax components with the governing provider fee schedule or invoice.",
            "A supported fee-basis record and a passing net-formula check.",
            supported=False,
        ),
    ),
    ExceptionCode.CURRENCY_MISMATCH: (
        ClosureDisposition.ESCALATE,
        _action(
            ExceptionCode.CURRENCY_MISMATCH,
            ClosureLane.FINANCE_CONTROL,
            "Verify the currency path",
            "Confirm the source currency and whether an authorised conversion occurred.",
            "A supported FX or conversion record linking both currencies and amounts.",
            supported=False,
        ),
    ),
    ExceptionCode.DUPLICATE_CONFLICT: (
        ClosureDisposition.ESCALATE,
        _action(
            ExceptionCode.DUPLICATE_CONFLICT,
            ClosureLane.DATA_QUALITY,
            "Quarantine the conflicting identity",
            "Compare both payloads at the source and determine which observation is authoritative.",
            "A versioned correction or supersession record; stored history must not be "
            "overwritten.",
            supported=False,
        ),
    ),
    ExceptionCode.OUT_OF_ORDER_EVENT: (
        ClosureDisposition.INVESTIGATE_SOURCE,
        _action(
            ExceptionCode.OUT_OF_ORDER_EVENT,
            ClosureLane.PSP_OPERATIONS,
            "Verify lifecycle timing",
            "Check provider occurrence timestamps and the capture-to-return sequence.",
            "Authoritative lifecycle evidence whose temporal order satisfies the contract.",
            supported=False,
        ),
    ),
    ExceptionCode.PARTIAL_REFUND: (
        ClosureDisposition.ESCALATE,
        _action(
            ExceptionCode.PARTIAL_REFUND,
            ClosureLane.FINANCE_CONTROL,
            "Account for the residual balance",
            "Explain the portion of the capture that remains after the recorded refund.",
            "A supported adjustment or terminal lifecycle record accounting for the remaining "
            "balance.",
            supported=False,
        ),
    ),
    ExceptionCode.TIMING_PENDING: (
        ClosureDisposition.MONITOR,
        _action(
            ExceptionCode.TIMING_PENDING,
            ClosureLane.EVIDENCE_OPERATIONS,
            "Wait for the settlement window",
            "Keep the line open and check again when the expected window expires or evidence "
            "arrives.",
            "A settlement record, or an elapsed window that activates the late-settlement rule.",
            supported=True,
        ),
    ),
    ExceptionCode.UNMAPPED_REFERENCE: (
        ClosureDisposition.COLLECT_EVIDENCE,
        _action(
            ExceptionCode.UNMAPPED_REFERENCE,
            ClosureLane.DATA_QUALITY,
            "Resolve the broken reference",
            "Look up the provider reference in an authoritative export and preserve its exact "
            "identity.",
            "A stored fact whose ID, source system, and payload hash all match the citation.",
            supported=True,
        ),
    ),
    ExceptionCode.MALFORMED_RECORD: (
        ClosureDisposition.COLLECT_EVIDENCE,
        _action(
            ExceptionCode.MALFORMED_RECORD,
            ClosureLane.DATA_QUALITY,
            "Correct the source export",
            "Fix the rejected document at its source and submit the complete document again.",
            "An accepted import receipt for a document satisfying the declared schema.",
            supported=True,
        ),
    ),
    ExceptionCode.UNSUPPORTED_STATE: (
        ClosureDisposition.ESCALATE,
        _action(
            ExceptionCode.UNSUPPORTED_STATE,
            ClosureLane.FINANCE_CONTROL,
            "Escalate the unsupported lifecycle",
            "Record the case for policy review without forcing it into a nearby supported state.",
            "A reviewed contract extension with fixtures and invariants for this lifecycle shape.",
            supported=False,
        ),
    ),
    ExceptionCode.INSUFFICIENT_EVIDENCE: (
        ClosureDisposition.COLLECT_EVIDENCE,
        _action(
            ExceptionCode.INSUFFICIENT_EVIDENCE,
            ClosureLane.EVIDENCE_OPERATIONS,
            "Request the missing evidence",
            "Read the certificate, identify each unverified citation or unrun invariant, and "
            "request it.",
            "All cited facts present and every required invariant evaluable.",
            supported=True,
        ),
    ),
}


def playbook_for(code: ExceptionCode) -> tuple[ClosureDisposition, ClosureAction]:
    """Return the versioned deterministic playbook for one exception code."""
    return _PLAYBOOKS[code]


def _generic_action(decision: ReconciliationDecision) -> ClosureAction:
    """Route the theoretically possible code-free unresolved decision loudly."""
    reasons = ", ".join(reason.value for reason in decision.reason_codes)
    return ClosureAction(
        action_code="UNRESOLVED_CERTIFICATE",
        owner_lane=ClosureLane.FINANCE_CONTROL,
        title="Inspect the unresolved certificate",
        instruction="Review each unverified citation and required invariant before taking action.",
        evidence_required=f"Evidence that clears the recorded reason codes: {reasons}.",
        supported_by_current_contract=False,
    )


def build_closure_plan(decision: ReconciliationDecision) -> ClosurePlan:
    """Derive a close plan from one verified decision without changing it."""
    if decision.status is DecisionStatus.RESOLVED:
        return ClosurePlan(
            baseline_status=decision.status,
            disposition=ClosureDisposition.NO_ACTION,
            primary_owner=ClosureLane.NONE,
            headline="No finance-ops follow-up is required for this decision.",
            blocking_codes=(),
            actions=(),
            requires_new_run=False,
            resolution_gate=(
                "Already resolved by the recorded certificate. Later evidence creates a new run; "
                "it never edits this one."
            ),
        )

    codes = tuple(sorted(set(decision.exception_codes), key=precedence_rank))
    if not codes:
        action = _generic_action(decision)
        return ClosurePlan(
            baseline_status=decision.status,
            disposition=ClosureDisposition.ESCALATE,
            primary_owner=action.owner_lane,
            headline="The certificate needs finance-control review.",
            blocking_codes=tuple(reason.value for reason in decision.reason_codes),
            actions=(action,),
            requires_new_run=True,
            resolution_gate=RESOLUTION_GATE,
        )

    disposition, primary = playbook_for(codes[0])
    actions = tuple(playbook_for(code)[1] for code in codes)
    return ClosurePlan(
        baseline_status=decision.status,
        disposition=disposition,
        primary_owner=primary.owner_lane,
        headline=primary.title,
        blocking_codes=tuple(code.value for code in codes),
        actions=actions,
        requires_new_run=True,
        resolution_gate=RESOLUTION_GATE,
    )
