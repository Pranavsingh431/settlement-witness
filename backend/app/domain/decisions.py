"""Reconciliation decisions, and the verifier that decides their status.

The central rule of this system lives here.

A decision may be RESOLVED only when every evidence reference resolved to a real
source fact, and every required deterministic invariant reached a determinate,
passing answer. Nothing a model produces can satisfy either condition. Prose, a
confidence score and a chain of reasoning are absent from this contract by
construction, so there is no field to smuggle them through and no code path that
could weigh them against an invariant.

Two layers do two different jobs, and confusing them is the mistake this module
is arranged to prevent.

**Structural validation** happens in :class:`ReconciliationDecision`. It checks
that a decision is internally coherent and that its status is exactly the status
its recorded backing implies. It cannot check that a cited fact exists, because
a validator has no way to go and look.

**Source-fact verification** happens in :func:`verify_decision`, which takes the
available facts as an argument and resolves every citation against them. A
decision carries the resulting certificate, and RESOLVED requires every citation
in it to have verified. So a hand-built RESOLVED decision is not an oversight
that slips through; it requires fabricating verification results, which is a
deliberate lie rather than a missing check.

Phase 1 has no persistence, so the caller supplies the facts. Phase 2 storage
will supply the index instead. The boundary does not move.
"""

from collections.abc import Iterable, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.codes import ExceptionCode, ReasonCode, highest_precedence
from app.domain.evidence import (
    EvidenceRef,
    EvidenceVerification,
    SourceFactIndex,
    exception_codes_for,
    verify_evidence,
)
from app.domain.facts import SourceFact
from app.domain.invariants import (
    REQUIRED_FOR_RESOLUTION,
    InvariantId,
    InvariantOutcome,
    InvariantResult,
)
from app.domain.primitives import (
    DecisionId,
    EventId,
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
    """The backing carries a reported finding or a failed invariant.

    It does not assert that the records needed to judge the line were present.
    `derive_status` reads the exception codes before it looks at the evidence,
    so a candidate citing nothing at all and carrying one ordinary code derives
    this status. The reason codes on such a decision still say `EVIDENCE_MISSING`,
    so the gap is recorded rather than hidden, but the status alone is not proof
    of a complete backing.

    Whether a bare code with no citations should remain constructible is an open
    question about this contract, not about any one caller. See "An open question
    about bare exception codes" in `docs/domain-contract.md`."""

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

STATUS_BY_EXCEPTION_CODE = MappingProxyType(_STATUS_BY_CODE)


def derive_status(
    *,
    evidence: Sequence[EvidenceRef],
    invariant_results: Sequence[InvariantResult],
    exception_codes: Sequence[ExceptionCode],
    evidence_verification: Sequence[EvidenceVerification],
) -> DecisionStatus:
    """Return the only status the given backing supports.

    This is the authority. A status is never chosen by a caller and never
    asserted; it is computed from what actually backs the decision, and
    :class:`ReconciliationDecision` refuses any status that disagrees with what
    this function returns for the same backing.

    The order of the checks is the contract's precedence rules:

    1. Citations that did not resolve imply their own exception codes, so a
       decision cannot escape them by omitting them.
    2. The highest precedence code among those and the declared ones decides the
       status. This is why EXCEPTION with only TIMING_PENDING is impossible, and
       why INSUFFICIENT_EVIDENCE alongside MALFORMED_RECORD is impossible.
    3. With no code and no evidence, the honest answer is INSUFFICIENT_EVIDENCE.
    4. A required invariant that is absent or INSUFFICIENT_INPUT means the same.
    5. Any failed invariant means EXCEPTION.
    6. RESOLVED is what remains. A resolution is the absence of any reason not to
       resolve, not a positive claim a caller may make.

    Args:
        evidence: The citations the decision rests on.
        invariant_results: Outcomes of the checks that were run.
        exception_codes: Codes raised while examining the case.
        evidence_verification: Results of resolving the citations against real
            source facts. An empty sequence alongside non-empty evidence means
            the citations were never checked, which is not a pass.

    Returns:
        The status implied by the backing.
    """
    implied = exception_codes_for(evidence, evidence_verification)
    effective = tuple(dict.fromkeys((*exception_codes, *implied)))

    deciding = highest_precedence(effective)
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


class DecisionCandidate(BaseModel):
    """A decision before its citations have been checked and its status decided.

    A candidate is what a caller builds. It is structurally validated, so it
    cannot be incoherent, but it carries neither a status nor reason codes:
    neither is a caller's to supply. Pass it to :func:`verify_decision` with the
    available facts to get a :class:`ReconciliationDecision`.

    Exception codes are different and are carried across. They are findings a
    caller made while examining the case, such as noticing a refund dated before
    its capture, and the verifier has no way to rediscover them. Reason codes
    are the verifier's own account of which rule fired, so it derives them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: DecisionId
    schema_version: DomainSchemaVersion = DOMAIN_SCHEMA_VERSION
    subject_settlement_line_id: SettlementLineId
    linked_source_record_ids: tuple[SourceRecordId, ...] = ()
    linked_event_ids: tuple[EventId, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    invariant_results: tuple[InvariantResult, ...] = ()
    exception_codes: tuple[ExceptionCode, ...] = ()
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _check_structure(self) -> Self:
        """Check the rules that hold whatever the status turns out to be."""
        _check_no_repeated_invariants(self.invariant_results)
        _check_evidence_is_unique_and_linked(self.evidence, self.linked_source_record_ids)
        return self


class ReconciliationDecision(BaseModel):
    """One verified decision about one settlement line.

    The subject is a single settlement line. The evidence and linked events may
    be many, because a line's correctness can depend on a capture, later refunds,
    the payout it belongs to and a bank credit, all at once.

    ``status`` is not a field a caller chooses. It must equal what
    :func:`derive_status` returns for this decision's own backing, and
    construction fails otherwise.

    ``evidence_verification`` is the certificate that the citations were resolved
    against real facts. Building this model does not produce it. Only
    :func:`verify_decision` does, because only it is given the facts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: DecisionId
    schema_version: DomainSchemaVersion = DOMAIN_SCHEMA_VERSION
    status: DecisionStatus
    subject_settlement_line_id: SettlementLineId
    linked_source_record_ids: tuple[SourceRecordId, ...] = ()
    linked_event_ids: tuple[EventId, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    evidence_verification: tuple[EvidenceVerification, ...] = ()
    invariant_results: tuple[InvariantResult, ...] = ()
    exception_codes: tuple[ExceptionCode, ...] = ()
    reason_codes: tuple[ReasonCode, ...]
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _check_decision_is_coherent(self) -> Self:
        """Enforce the structural rules and then the derived status."""
        if not self.reason_codes:
            message = "a decision must carry at least one reason_code"
            raise ValueError(message)

        _check_no_repeated_invariants(self.invariant_results)
        _check_evidence_is_unique_and_linked(self.evidence, self.linked_source_record_ids)
        self._check_verification_matches_evidence()
        self._check_status_is_derived()
        return self

    def _check_verification_matches_evidence(self) -> None:
        """Every citation has one result, and no result invents a citation."""
        cited = [reference.source_record_id for reference in self.evidence]
        checked = [result.source_record_id for result in self.evidence_verification]

        if len(checked) != len(set(checked)):
            message = "evidence_verification holds more than one result for the same record"
            raise ValueError(message)

        invented = sorted(set(checked) - set(cited))
        if invented:
            message = f"evidence_verification names records the decision does not cite: {invented}"
            raise ValueError(message)

    def _check_status_is_derived(self) -> None:
        """The status must be the one the backing implies, not one that was picked."""
        derived = derive_status(
            evidence=self.evidence,
            invariant_results=self.invariant_results,
            exception_codes=self.exception_codes,
            evidence_verification=self.evidence_verification,
        )
        if self.status is not derived:
            message = (
                f"status {self.status.value} contradicts the backing, which implies "
                f"{derived.value}; a status is derived from evidence, invariant "
                "results and exception codes, never asserted"
            )
            raise ValueError(message)

    @property
    def verified_evidence_count(self) -> int:
        """Return how many citations resolved to the fact they named."""
        return sum(1 for result in self.evidence_verification if result.is_verified)


def _check_no_repeated_invariants(results: Sequence[InvariantResult]) -> None:
    """One invariant produces one result, so a decision cannot hold two."""
    seen = [result.invariant_id for result in results]
    if len(seen) != len(set(seen)):
        message = "invariant_results contains more than one result for the same invariant"
        raise ValueError(message)


def _check_evidence_is_unique_and_linked(
    evidence: Sequence[EvidenceRef], linked: Sequence[SourceRecordId]
) -> None:
    """Evidence names each record once, and only records the decision links."""
    cited = [reference.source_record_id for reference in evidence]
    if len(cited) != len(set(cited)):
        message = "evidence cites the same source record more than once"
        raise ValueError(message)

    known = set(linked)
    orphans = sorted(record_id for record_id in cited if record_id not in known)
    if orphans:
        message = (
            "every evidence reference must name a source record in "
            f"linked_source_record_ids; unlinked: {orphans}"
        )
        raise ValueError(message)


def verify_decision(
    candidate: DecisionCandidate,
    facts: SourceFactIndex | Iterable[SourceFact],
) -> ReconciliationDecision:
    """Resolve a candidate's citations against real facts and decide its status.

    This is the only path that produces a decision whose evidence has actually
    been checked. It is pure: it reads the candidate and the facts, touches no
    global state, opens no connection, and returns a new object.

    Citations that fail to resolve contribute their own exception codes and
    reason codes, so a decision cannot reach RESOLVED by citing a fact that is
    not there.

    Args:
        candidate: The structurally validated draft.
        facts: The source facts available, as a prepared index or any iterable.
            Phase 2 storage will supply this; Phase 1 callers pass it directly.

    Returns:
        A decision whose status was derived, carrying the verification
        certificate for every citation it made.
    """
    verification = verify_evidence(candidate.evidence, facts)
    implied_codes = exception_codes_for(candidate.evidence, verification)

    exception_codes = tuple(dict.fromkeys((*candidate.exception_codes, *implied_codes)))

    status = derive_status(
        evidence=candidate.evidence,
        invariant_results=candidate.invariant_results,
        exception_codes=exception_codes,
        evidence_verification=verification,
    )

    reason_codes = derive_reason_codes(
        status=status,
        evidence=candidate.evidence,
        invariant_results=candidate.invariant_results,
        exception_codes=exception_codes,
        evidence_verification=verification,
    )

    return ReconciliationDecision(
        decision_id=candidate.decision_id,
        schema_version=candidate.schema_version,
        status=status,
        subject_settlement_line_id=candidate.subject_settlement_line_id,
        linked_source_record_ids=candidate.linked_source_record_ids,
        linked_event_ids=candidate.linked_event_ids,
        evidence=candidate.evidence,
        evidence_verification=verification,
        invariant_results=candidate.invariant_results,
        exception_codes=exception_codes,
        reason_codes=reason_codes,
        created_at=candidate.created_at,
    )


#: Canonical order for reason codes, taken from the enum declaration order. A
#: decision's reason codes are always emitted in this order, so two runs over the
#: same backing produce byte identical output.
_REASON_ORDER: dict[ReasonCode, int] = {code: rank for rank, code in enumerate(ReasonCode)}


def derive_reason_codes(
    *,
    status: DecisionStatus,
    evidence: Sequence[EvidenceRef],
    invariant_results: Sequence[InvariantResult],
    exception_codes: Sequence[ExceptionCode],
    evidence_verification: Sequence[EvidenceVerification],
) -> tuple[ReasonCode, ...]:
    """Return the reason codes the backing implies, in canonical order.

    A reason code says which rule fired. It is the verifier's own account of the
    decision, so it is computed here rather than accepted from a caller. A
    caller that could supply its own reason codes could describe a decision as
    having been reached for reasons that had nothing to do with it, and nothing
    would contradict them.

    This is a pure function of the arguments, and the arguments are themselves
    derived from evidence and invariants. So two decisions with the same backing
    carry the same reasons, whatever the callers that built them believed.

    Args:
        status: The status already derived from the same backing.
        evidence: The citations the decision rests on.
        invariant_results: Outcomes of the checks that were run.
        exception_codes: Codes raised, including any implied by verification.
        evidence_verification: Results of resolving the citations.

    Returns:
        At least one reason code, ordered canonically and without repeats.
    """
    reasons: set[ReasonCode] = set()

    for citation in evidence_verification:
        if citation.reason_code is not None:
            reasons.add(citation.reason_code)

    if not evidence:
        reasons.add(ReasonCode.EVIDENCE_MISSING)

    by_id = {check.invariant_id: check for check in invariant_results}
    for invariant_id in REQUIRED_FOR_RESOLUTION:
        required = by_id.get(invariant_id)
        if required is None:
            reasons.add(ReasonCode.REQUIRED_INVARIANT_NOT_EVALUATED)
        elif required.outcome is InvariantOutcome.INSUFFICIENT_INPUT:
            reasons.add(ReasonCode.REQUIRED_INVARIANT_MISSING_INPUT)

    failed = [check for check in invariant_results if check.outcome is InvariantOutcome.FAILED]
    if failed:
        reasons.add(ReasonCode.REQUIRED_INVARIANT_FAILED)
    # A FAILED result always names its reason: InvariantResult refuses one that
    # does not. filter drops the None the type allows and the model prevents.
    reasons.update(filter(None, (check.reason_code for check in failed)))

    if ExceptionCode.TIMING_PENDING in exception_codes:
        reasons.add(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW)

    if status is DecisionStatus.RESOLVED:
        reasons.add(ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED)

    if not reasons and exception_codes:
        # Nothing failed and nothing was missing. The case carries a finding
        # that this system cannot settle, and the exception codes say what.
        reasons.add(ReasonCode.EXCEPTION_CODE_REPORTED)

    if not reasons:
        # Unreachable for a status this module derived: every non-resolved
        # status needs a code or a failure, and both produce a reason. It is
        # reachable by calling this with a status that does not match the
        # backing, and the honest answer there is to refuse rather than invent a
        # reason. An earlier version filled the gap with a fixed code per
        # status, which named a rule that had never fired.
        message = (
            f"status {status.value} does not follow from this backing, so no "
            "reason code describes it"
        )
        raise ValueError(message)

    return tuple(sorted(reasons, key=lambda code: _REASON_ORDER[code]))


def check_decision_evidence(decision: ReconciliationDecision) -> InvariantResult:
    """INV-006: a resolved decision is backed by verified source facts.

    Implemented here rather than in :mod:`app.domain.invariants` because it reads
    a decision, and having the two modules import each other would be worse than
    splitting the catalogue from two of its checks.
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
    if decision.verified_evidence_count != len(decision.evidence):
        return InvariantResult(
            invariant_id=InvariantId.INV_006,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.EVIDENCE_NOT_VERIFIED,
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
