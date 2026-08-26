"""Typed request and response models for the API.

Responses expose evidence references and the invariant certificate, not the raw
canonical payloads behind them. A citation names a record and its payload hash,
which is what makes a decision checkable; serving the payload itself would put
merchant data on an endpoint that exists to explain a conclusion.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import EvidenceOutcome
from app.domain.invariants import InvariantId, InvariantOutcome
from app.reconciliation.runs import PersistedRun

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class RunSummary(BaseModel):
    """One reconciliation run, without its decisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    snapshot_fingerprint: str
    baseline_version: str
    domain_schema_version: str
    parser_version: str
    created_at: datetime
    as_of: datetime
    fact_count: int
    settlement_line_count: int
    decision_count: int
    status_counts: dict[str, int]
    exception_counts: dict[str, int]

    @classmethod
    def of(cls, run: PersistedRun) -> "RunSummary":
        """Return the public view of a persisted run.

        The canonical run key is deliberately not exposed. It is an internal
        idempotency identity, and publishing it would invite callers to depend
        on how it is computed.
        """
        return cls(
            run_id=run.run_id,
            snapshot_fingerprint=run.snapshot_fingerprint,
            baseline_version=run.baseline_version,
            domain_schema_version=run.domain_schema_version,
            parser_version=run.parser_version,
            created_at=run.created_at,
            as_of=run.as_of,
            fact_count=run.fact_count,
            settlement_line_count=run.settlement_line_count,
            decision_count=run.decision_count,
            status_counts=dict(sorted(run.status_counts.items())),
            exception_counts=dict(sorted(run.exception_counts.items())),
        )


class EvidenceReference(BaseModel):
    """One citation, and whether it resolved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: str
    source_system: str
    payload_hash: str
    verification_outcome: EvidenceOutcome | None
    """None when the decision carries no result for this citation."""


class InvariantCheck(BaseModel):
    """One recorded invariant result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invariant_id: InvariantId
    outcome: InvariantOutcome
    reason_code: ReasonCode | None
    expected_minor: int | None
    observed_minor: int | None


class DecisionView(BaseModel):
    """One decision, with its evidence and invariant certificate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    schema_version: str
    status: DecisionStatus
    subject_settlement_line_id: str
    linked_source_record_ids: list[str]
    linked_event_ids: list[str]
    evidence: list[EvidenceReference]
    invariant_results: list[InvariantCheck]
    exception_codes: list[ExceptionCode]
    reason_codes: list[ReasonCode]
    created_at: datetime
    verified_evidence_count: int

    @classmethod
    def of(cls, decision: ReconciliationDecision) -> "DecisionView":
        """Return the public view of a decision.

        Evidence and verification are joined into one list, because a citation
        and whether it resolved are the same fact about the same record, and
        making a caller line up two parallel arrays invites mistakes.
        """
        verification = {
            result.source_record_id: result.outcome for result in decision.evidence_verification
        }
        return cls(
            decision_id=decision.decision_id,
            schema_version=decision.schema_version,
            status=decision.status,
            subject_settlement_line_id=decision.subject_settlement_line_id,
            linked_source_record_ids=list(decision.linked_source_record_ids),
            linked_event_ids=list(decision.linked_event_ids),
            evidence=[
                EvidenceReference(
                    source_record_id=reference.source_record_id,
                    source_system=reference.source_system.value,
                    payload_hash=reference.payload_hash,
                    verification_outcome=verification.get(reference.source_record_id),
                )
                for reference in decision.evidence
            ],
            invariant_results=[
                InvariantCheck(
                    invariant_id=result.invariant_id,
                    outcome=result.outcome,
                    reason_code=result.reason_code,
                    expected_minor=result.expected_minor,
                    observed_minor=result.observed_minor,
                )
                for result in decision.invariant_results
            ],
            exception_codes=list(decision.exception_codes),
            reason_codes=list(decision.reason_codes),
            created_at=decision.created_at,
            verified_evidence_count=decision.verified_evidence_count,
        )


class RunDetail(BaseModel):
    """A run and the decisions it reached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RunSummary
    decisions: list[DecisionView]
    filtered: bool
    """True when a status or exception filter narrowed the list.

    Reported so a caller cannot mistake a filtered view for the whole run."""


class RunPage(BaseModel):
    """A page of run summaries, newest first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runs: list[RunSummary]
    total: int
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(ge=0)


class ErrorResponse(BaseModel):
    """What a failure looks like.

    A code and a sentence. No stack trace, no SQL, and nothing about the shape
    of the database.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str
    detail: str
