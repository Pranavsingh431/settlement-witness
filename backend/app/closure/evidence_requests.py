"""Build a shareable request for proof without manufacturing a conclusion.

An evidence request is an operational handoff, not a financial record. It
names the exact cited identities, the owner, the proof sought and the closure
gate. It never contains raw source payloads, a proposed amount, an approval or
an instruction to edit a decision. Receiving a package back is not evidence:
the authoritative source record still has to be imported and reconciled into a
new immutable run.
"""

from pydantic import BaseModel, ConfigDict, model_validator

from app.closure.plans import ClosureAction, ClosurePlan, build_closure_plan
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import EvidenceOutcome

EVIDENCE_REQUEST_PACKAGE_VERSION = "1.0.0"

REQUEST_NOTICE = (
    "This package requests evidence. It does not approve an adjustment, change the recorded "
    "decision, or close the work. Only authoritative evidence imported into a new reconciliation "
    "run can satisfy the closure gate."
)


class RequestedEvidenceReference(BaseModel):
    """One existing citation included for the recipient to locate the right record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: str
    source_system: str
    payload_hash: str
    verification_outcome: EvidenceOutcome | None


class EvidenceRequestPackage(BaseModel):
    """A deterministic, non-authoritative package for one unresolved decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_version: str = EVIDENCE_REQUEST_PACKAGE_VERSION
    decision_id: str
    subject_settlement_line_id: str
    baseline_status: DecisionStatus
    closure_plan_version: str
    primary_owner: str
    requested_actions: tuple[ClosureAction, ...]
    cited_evidence: tuple[RequestedEvidenceReference, ...]
    acceptance_condition: str
    request_notice: str = REQUEST_NOTICE

    @model_validator(mode="after")
    def _cannot_repackage_a_resolution_as_open_work(self) -> "EvidenceRequestPackage":
        if self.baseline_status is DecisionStatus.RESOLVED:
            raise ValueError("a resolved decision does not need an evidence request")
        if not self.requested_actions:
            raise ValueError("an evidence request must name at least one bounded action")
        return self


def _references(decision: ReconciliationDecision) -> tuple[RequestedEvidenceReference, ...]:
    outcomes = {
        result.source_record_id: result.outcome for result in decision.evidence_verification
    }
    return tuple(
        RequestedEvidenceReference(
            source_record_id=reference.source_record_id,
            source_system=reference.source_system.value,
            payload_hash=reference.payload_hash,
            verification_outcome=outcomes.get(reference.source_record_id),
        )
        for reference in decision.evidence
    )


def build_evidence_request(decision: ReconciliationDecision) -> EvidenceRequestPackage:
    """Return a package derived entirely from the decision's close plan.

    Args:
        decision: The immutable baseline conclusion being handed to operations.

    Raises:
        ValueError: If the conclusion already resolved and has no work to hand off.
    """
    plan: ClosurePlan = build_closure_plan(decision)
    if not plan.requires_new_run:
        raise ValueError("the recorded decision is already resolved; no evidence request is needed")

    return EvidenceRequestPackage(
        decision_id=decision.decision_id,
        subject_settlement_line_id=decision.subject_settlement_line_id,
        baseline_status=decision.status,
        closure_plan_version=plan.plan_version,
        primary_owner=plan.primary_owner.value,
        requested_actions=plan.actions,
        cited_evidence=_references(decision),
        acceptance_condition=plan.resolution_gate,
    )
