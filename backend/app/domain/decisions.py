"""Reconciliation decisions and the verifier rule that governs them.

The central rule of this system lives here.

A decision may be RESOLVED only when every required evidence reference exists
and every applicable deterministic invariant reached a determinate, passing
answer. Nothing a model produces can satisfy either condition. Prose, a
confidence score and a chain of reasoning are all absent from this contract by
construction, so there is no field through which they could be smuggled in and
no code path that could weigh them against an invariant.

The rule is enforced by the model itself, not by a separate step a caller has to
remember to run. A ReconciliationDecision that claims RESOLVED without the
backing is not merely flagged, it cannot be constructed.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.codes import ExceptionCode, ReasonCode, highest_precedence
from app.domain.facts import SourceSystem
from app.domain.invariants import (
    REQUIRED_FOR_RESOLUTION,
    InvariantId,
    InvariantOutcome,
    InvariantResult,
)
from app.domain.primitives import (
    DecisionId,
    EventId,
    PayloadHash,
    SettlementLineId,
    SourceRecordId,
    UtcTimestamp,
)
from app.domain.version import DOMAIN_SCHEMA_VERSION, DomainSchemaVersion


class DecisionStatus(StrEnum):
    """The four states a decision may end in. There is no fifth."""

    RESOLVED = "RESOLVED"
    """Evidence-complete and invariant-clean. The only status that asserts the
    line is correctly settled."""

    EXCEPTION = "EXCEPTION"
    """Something is demonstrably wrong, and the records needed to say so were
    present."""

    PENDING = "PENDING"
    """Nothing is wrong yet. The case is inside its expected window and is
    waiting for information that is expected to arrive."""

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    """The evidence admits more than one explanation. Reported honestly rather
    than collapsed into the nearest exception."""


#: Which status an exception code implies when it is the highest precedence code
#: present. Everything not listed here means EXCEPTION.
_STATUS_BY_CODE: dict[ExceptionCode, DecisionStatus] = {
    ExceptionCode.TIMING_PENDING: DecisionStatus.PENDING,
    ExceptionCode.INSUFFICIENT_EVIDENCE: DecisionStatus.INSUFFICIENT_EVIDENCE,
}

STATUS_BY_EXCEPTION_CODE: Mapping[ExceptionCode, DecisionStatus] = MappingProxyType(_STATUS_BY_CODE)


class EvidenceRef(BaseModel):
    """A pointer to one source fact that supports a decision.

    Evidence is a reference to an observation, never a description of one. This
    model has no free text field and forbids extra keys, so a caller cannot
    attach a summary, a justification or model output to a piece of evidence.
    The payload hash is carried so that a stored decision can be checked against
    the fact it cited, and a later rewrite of that fact would be detectable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: SourceRecordId
    source_system: SourceSystem
    payload_hash: PayloadHash


class ReconciliationDecision(BaseModel):
    """One decision about one settlement line.

    The subject is a single settlement line. The evidence and linked events may
    be many, because a line's correctness can depend on a capture, later
    refunds, the payout it belongs to and a bank credit, all at once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: DecisionId
    schema_version: DomainSchemaVersion = DOMAIN_SCHEMA_VERSION
    status: DecisionStatus
    subject_settlement_line_id: SettlementLineId
    linked_source_record_ids: tuple[SourceRecordId, ...] = ()
    linked_event_ids: tuple[EventId, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    invariant_results: tuple[InvariantResult, ...] = ()
    exception_codes: tuple[ExceptionCode, ...] = ()
    reason_codes: tuple[ReasonCode, ...]
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _check_decision_is_coherent(self) -> Self:
        """Enforce the verifier rule and the structural rules around it."""
        self._check_reason_codes_present()
        self._check_no_repeated_invariants()
        self._check_evidence_is_linked()
        self._check_status_matches_backing()
        return self

    def _check_reason_codes_present(self) -> None:
        """Every decision says why it reached its status."""
        if not self.reason_codes:
            message = "a decision must carry at least one reason_code"
            raise ValueError(message)

    def _check_no_repeated_invariants(self) -> None:
        """One invariant produces one result, so a decision cannot hold two."""
        seen = [result.invariant_id for result in self.invariant_results]
        if len(seen) != len(set(seen)):
            message = "invariant_results contains more than one result for the same invariant"
            raise ValueError(message)

    def _check_evidence_is_linked(self) -> None:
        """Evidence must point at a source record the decision also links."""
        linked = set(self.linked_source_record_ids)
        orphans = sorted(
            {ref.source_record_id for ref in self.evidence if ref.source_record_id not in linked}
        )
        if orphans:
            message = (
                "every evidence reference must name a source record in "
                f"linked_source_record_ids; unlinked: {orphans}"
            )
            raise ValueError(message)

    def _check_status_matches_backing(self) -> None:
        """Check the status against what actually backs it."""
        if self.status is DecisionStatus.RESOLVED:
            self._check_resolution_is_earned()
            return

        if self.status is DecisionStatus.EXCEPTION and not self.exception_codes:
            message = "an EXCEPTION decision must carry at least one exception code"
            raise ValueError(message)

        if (
            self.status is DecisionStatus.INSUFFICIENT_EVIDENCE
            and ExceptionCode.INSUFFICIENT_EVIDENCE not in self.exception_codes
        ):
            message = (
                "an INSUFFICIENT_EVIDENCE decision must carry the "
                "INSUFFICIENT_EVIDENCE exception code"
            )
            raise ValueError(message)

        if self.status is DecisionStatus.PENDING:
            stronger = sorted(
                code.value
                for code in self.exception_codes
                if code is not ExceptionCode.TIMING_PENDING
            )
            if stronger:
                message = (
                    "a PENDING decision may only carry TIMING_PENDING; "
                    f"these belong to a stronger status: {stronger}"
                )
                raise ValueError(message)

    def _check_resolution_is_earned(self) -> None:
        """The central rule. A resolution has to be paid for in evidence.

        Raises:
            ValueError: If any of the four conditions is unmet.
        """
        if not self.evidence:
            message = "a RESOLVED decision must cite at least one source fact as evidence"
            raise ValueError(message)

        if self.exception_codes:
            codes = sorted(code.value for code in self.exception_codes)
            message = f"a RESOLVED decision cannot also carry exception codes: {codes}"
            raise ValueError(message)

        results = {result.invariant_id: result for result in self.invariant_results}

        missing = sorted(
            invariant_id.value
            for invariant_id in REQUIRED_FOR_RESOLUTION
            if invariant_id not in results
        )
        if missing:
            message = (
                "a RESOLVED decision must carry a result for every required "
                f"invariant; missing: {missing}"
            )
            raise ValueError(message)

        undetermined = sorted(
            f"{invariant_id.value}={results[invariant_id].outcome.value}"
            for invariant_id in REQUIRED_FOR_RESOLUTION
            if not results[invariant_id].is_determinate
        )
        if undetermined:
            message = (
                "a RESOLVED decision requires every required invariant to pass "
                f"or be not applicable; these did not: {undetermined}"
            )
            raise ValueError(message)

        failed = sorted(
            result.invariant_id.value
            for result in self.invariant_results
            if result.outcome is InvariantOutcome.FAILED
        )
        if failed:
            message = f"a RESOLVED decision cannot carry a failed invariant result: {failed}"
            raise ValueError(message)


def check_decision_evidence(decision: ReconciliationDecision) -> InvariantResult:
    """INV-006: a resolved decision is backed by source facts.

    Implemented here rather than in :mod:`app.domain.invariants` because it
    reads a decision, and having the two modules import each other would be
    worse than splitting the catalogue from two of its checks.
    """
    if decision.status is not DecisionStatus.RESOLVED:
        return InvariantResult(
            invariant_id=InvariantId.INV_006,
            outcome=InvariantOutcome.NOT_APPLICABLE,
        )
    if not decision.evidence:
        return InvariantResult(
            invariant_id=InvariantId.INV_006,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.EVIDENCE_MISSING,
        )
    linked = set(decision.linked_source_record_ids)
    if any(ref.source_record_id not in linked for ref in decision.evidence):
        return InvariantResult(
            invariant_id=InvariantId.INV_006,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.EVIDENCE_NOT_LINKED,
        )
    return InvariantResult(invariant_id=InvariantId.INV_006, outcome=InvariantOutcome.PASSED)


def check_decision_invariants(decision: ReconciliationDecision) -> InvariantResult:
    """INV-007: a resolved decision carries passing required invariant results."""
    if decision.status is not DecisionStatus.RESOLVED:
        return InvariantResult(
            invariant_id=InvariantId.INV_007,
            outcome=InvariantOutcome.NOT_APPLICABLE,
        )

    results = {result.invariant_id: result for result in decision.invariant_results}

    if any(invariant_id not in results for invariant_id in REQUIRED_FOR_RESOLUTION):
        return InvariantResult(
            invariant_id=InvariantId.INV_007,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.REQUIRED_INVARIANT_NOT_EVALUATED,
        )
    if any(
        results[invariant_id].outcome is InvariantOutcome.INSUFFICIENT_INPUT
        for invariant_id in REQUIRED_FOR_RESOLUTION
    ):
        return InvariantResult(
            invariant_id=InvariantId.INV_007,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.REQUIRED_INVARIANT_MISSING_INPUT,
        )
    if any(result.outcome is InvariantOutcome.FAILED for result in decision.invariant_results):
        return InvariantResult(
            invariant_id=InvariantId.INV_007,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.REQUIRED_INVARIANT_FAILED,
        )
    return InvariantResult(invariant_id=InvariantId.INV_007, outcome=InvariantOutcome.PASSED)


def derive_status(
    *,
    evidence: Sequence[EvidenceRef],
    invariant_results: Sequence[InvariantResult],
    exception_codes: Sequence[ExceptionCode],
) -> DecisionStatus:
    """Return the only status the given backing supports.

    This is the verifier rule expressed as a function, so a caller never has to
    choose a status. RESOLVED is what remains when nothing else applies, which
    is the correct direction: a resolution is the absence of any reason not to
    resolve, not a positive claim a caller may assert.

    Args:
        evidence: Source facts cited in support.
        invariant_results: Outcomes of the checks that were run.
        exception_codes: Exception codes raised while examining the case.

    Returns:
        The status implied by the backing.
    """
    deciding = highest_precedence(exception_codes)
    if deciding is not None:
        return STATUS_BY_EXCEPTION_CODE.get(deciding, DecisionStatus.EXCEPTION)

    if not evidence:
        return DecisionStatus.INSUFFICIENT_EVIDENCE

    results = {result.invariant_id: result for result in invariant_results}

    for invariant_id in REQUIRED_FOR_RESOLUTION:
        result = results.get(invariant_id)
        if result is None or result.outcome is InvariantOutcome.INSUFFICIENT_INPUT:
            return DecisionStatus.INSUFFICIENT_EVIDENCE
        if result.outcome is InvariantOutcome.FAILED:
            return DecisionStatus.EXCEPTION

    if any(result.outcome is InvariantOutcome.FAILED for result in invariant_results):
        return DecisionStatus.EXCEPTION

    return DecisionStatus.RESOLVED
