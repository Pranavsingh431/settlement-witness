"""Typed request and response models for the API.

Responses expose evidence references and the invariant certificate, not the raw
canonical payloads behind them. A citation names a record and its payload hash,
which is what makes a decision checkable; serving the payload itself would put
merchant data on an endpoint that exists to explain a conclusion.
"""

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.banking.audits import PersistedBankFinalityAudit
from app.banking.finality import (
    BANK_FINALITY_VERSION,
    BankFinalityCertificate,
    BankFinalityOutcome,
)
from app.benchmark.metrics import Rate
from app.closure.plans import ClosureLane, ClosurePlan, build_closure_plan
from app.closure.triage import Workboard
from app.domain.banking import BankDirection
from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import EvidenceOutcome
from app.domain.facts import SourceRecordType, SourceSystem
from app.domain.invariants import InvariantId, InvariantOutcome
from app.ingestion.receipts import ImportOutcome, ImportReceipt, RowOutcome
from app.reconciliation.runs import PersistedRun
from app.review.events import (
    MAX_NOTE_LENGTH,
    REVIEW_CONTRACT_VERSION,
    ReviewAction,
    ReviewEvent,
    ReviewWorkflowState,
)
from app.review.service import AppendedReviewEvent, ReviewQueueItem, ReviewQueueSlice

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
    closure_plan: ClosurePlan
    """Operational recourse derived from this immutable decision."""

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
            closure_plan=build_closure_plan(decision),
        )


class RunDetail(BaseModel):
    """A run and the decisions it reached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RunSummary
    decisions: list[DecisionView]
    filtered: bool
    """True when a status or exception filter narrowed the list.

    Reported so a caller cannot mistake a filtered view for the whole run."""


class RunWorkboard(BaseModel):
    """Currency-separated prioritisation for the unresolved work in one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    snapshot_fingerprint: str
    workboard: Workboard


class RunPage(BaseModel):
    """A page of run summaries, newest first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runs: list[RunSummary]
    total: int
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(ge=0)


class RowOutcomeView(BaseModel):
    """What happened to one row of an imported document.

    Carries the row number, the outcome, and the reason where there is one. It
    never carries the cell values that caused it: an error message written by
    the parser names the column and the rule, which is what a person needs to
    fix their file, whereas echoing the cell would put document content on a
    response that exists to explain an outcome.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int
    outcome: RowOutcome
    source_record_id: str | None
    """Present only where the row got far enough to have an identity."""

    code: str | None
    detail: str | None


class ImportReceiptView(BaseModel):
    """One import attempt, as the API reports it.

    The receipt is the record of an attempt, not of a success. A rejected
    document has a receipt saying what was wrong with it, and that receipt is
    as much a part of the audit trail as an accepted one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    document_hash: str
    document_name: str
    source_system: SourceSystem
    source_record_type: SourceRecordType
    parser_version: str
    received_at: datetime
    outcome: ImportOutcome
    row_count: int
    accepted_count: int
    duplicate_count: int
    conflict_count: int
    rejected_count: int
    not_applied_count: int
    """Rows that were readable and were still not stored, because the document
    they belonged to was refused as a whole."""

    wrote_facts: bool
    """Whether this import added at least one fact.

    Derived from `accepted_count`, and checked against it, so it cannot become a
    second opinion about what happened. It is not a success flag: a
    `DUPLICATE_NO_OP` import wrote no facts and is the correct result, and
    `outcome` remains the only thing that says whether a document was taken."""

    failure_detail: str | None
    row_outcomes: list[RowOutcomeView]

    @model_validator(mode="after")
    def _counts_describe_the_rows(self) -> "ImportReceiptView":
        """Refuse a receipt whose summary disagrees with its own rows.

        A summary that can drift from the list beneath it is worse than no
        summary, because a reader checks the cheap number and not the long
        list. These are the same values from the same receipt, so disagreement
        means something rebuilt one of them wrongly, and serving it would put a
        false count into an audit trail.
        """
        tally = Counter(row.outcome for row in self.row_outcomes)
        expected = {
            "row_count": len(self.row_outcomes),
            "accepted_count": tally[RowOutcome.ACCEPTED],
            "duplicate_count": tally[RowOutcome.DUPLICATE_NO_OP],
            "conflict_count": tally[RowOutcome.DUPLICATE_CONFLICT],
            "rejected_count": tally[RowOutcome.REJECTED],
            "not_applied_count": tally[RowOutcome.NOT_APPLIED],
        }
        wrong = {
            name: (getattr(self, name), count)
            for name, count in expected.items()
            if getattr(self, name) != count
        }
        if wrong:
            message = f"receipt counts disagree with its row outcomes: {wrong}"
            raise ValueError(message)
        if self.wrote_facts != (self.accepted_count > 0):
            message = (
                f"wrote_facts is {self.wrote_facts} while {self.accepted_count} "
                "row(s) were accepted"
            )
            raise ValueError(message)
        return self

    @classmethod
    def of(cls, receipt: ImportReceipt) -> "ImportReceiptView":
        """Return the public view of an import receipt."""
        return cls(
            receipt_id=receipt.receipt_id,
            document_hash=receipt.document_hash,
            document_name=receipt.document_name,
            source_system=receipt.source_system,
            source_record_type=receipt.source_record_type,
            parser_version=receipt.parser_version,
            received_at=receipt.received_at,
            outcome=receipt.outcome,
            row_count=receipt.row_count,
            accepted_count=receipt.accepted_count,
            duplicate_count=receipt.duplicate_count,
            conflict_count=receipt.conflict_count,
            rejected_count=receipt.rejected_count,
            not_applied_count=sum(
                1 for result in receipt.row_results if result.outcome is RowOutcome.NOT_APPLIED
            ),
            wrote_facts=receipt.wrote_facts,
            failure_detail=receipt.failure_detail,
            row_outcomes=[
                RowOutcomeView(
                    row_number=result.row_number,
                    outcome=result.outcome,
                    source_record_id=result.source_record_id,
                    code=result.code,
                    detail=result.detail,
                )
                for result in receipt.row_results
            ],
        )


class ImportReceiptPage(BaseModel):
    """A page of import receipts, newest attempt first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipts: list[ImportReceiptView]
    total: int
    """How many receipts match the filters, not how many exist."""

    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(ge=0)
    filtered: bool
    """True when a filter narrowed the list.

    Reported so `total` cannot be read as the size of the whole history."""


class ErrorResponse(BaseModel):
    """What a failure looks like.

    A code and a sentence. No stack trace, no SQL, nothing about the shape of
    the database, and nothing echoed back from the request body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str
    detail: str


class ErrorEnvelope(BaseModel):
    """An error as it appears on the wire.

    Starlette wraps the detail of an `HTTPException` in a `detail` key, so every
    failure on this API arrives nested. This model says so, rather than
    documenting a flat body that no endpoint returns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    detail: ErrorResponse


BASELINE_IS_UNCHANGED = (
    "A review event records human workflow only. It does not change this "
    "decision's status, exception codes, invariant results or evidence, and "
    "closing a review does not resolve the line."
)
"""The sentence every review response carries.

On the wire rather than only in the interface, because a client written against
this API by somebody who never sees the screen has to be told the same thing.
It is a constant so the API tests and the interface tests assert one string
rather than two that could drift apart."""


class ReviewEventView(BaseModel):
    """One recorded review action.

    No actor field. There is no authentication in this application, so there is
    nobody to name, and a field holding a constant would read as one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    sequence: int
    """The order the database assigned. What the timeline is sorted by."""

    action: ReviewAction
    note: str | None
    """A sentence from a person. Plain text, and never markup."""

    recorded_at: datetime
    decision_fingerprint: str

    @classmethod
    def of(cls, event: ReviewEvent) -> "ReviewEventView":
        """Return the public view of a stored event."""
        return cls(
            event_id=event.event_id,
            sequence=event.sequence,
            action=event.action,
            note=event.note,
            recorded_at=event.recorded_at,
            decision_fingerprint=event.decision_fingerprint,
        )


class ReviewQueueItemView(BaseModel):
    """One reviewable decision, with the workflow recorded beside it.

    The decision is carried whole, as the same `DecisionView` every other
    endpoint serves. The workflow is carried separately and is never merged into
    it, because the two describe different things and a single flattened object
    would invite a client to render a workflow state where a status belongs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    decision: DecisionView
    """The baseline's conclusion, unchanged by anything in this response."""

    decision_fingerprint: str
    """Echo this back when appending an event, so a command aimed at another
    conclusion is refused rather than recorded against this one."""

    workflow_state: ReviewWorkflowState
    """Derived from `events` every time it is served. Never stored."""

    baseline_status: DecisionStatus
    """The same value as `decision.status`, named so it cannot be missed.

    Repeated deliberately. A client that reads only the workflow state would
    otherwise have to go looking for the conclusion, and the one mistake this
    endpoint must not make possible is showing a closed review as though the
    line were settled."""

    baseline_unchanged_note: str = BASELINE_IS_UNCHANGED
    events: list[ReviewEventView]

    @classmethod
    def of(cls, item: ReviewQueueItem) -> "ReviewQueueItemView":
        """Return the public view of one queue item."""
        return cls(
            run_id=item.run_id,
            decision=DecisionView.of(item.decision),
            decision_fingerprint=item.decision_fingerprint,
            workflow_state=item.workflow_state,
            baseline_status=item.decision.status,
            events=[ReviewEventView.of(event) for event in item.events],
        )


class ReviewQueuePage(BaseModel):
    """A page of the review queue for one recorded run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    review_contract_version: str = REVIEW_CONTRACT_VERSION
    items: list[ReviewQueueItemView]
    total: int
    """How many reviewable decisions the run holds, not how many it has."""

    open_total: int
    """How many of those are not closed."""

    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(ge=0)
    baseline_unchanged_note: str = BASELINE_IS_UNCHANGED

    @classmethod
    def of(
        cls, run_id: str, page: ReviewQueueSlice, *, limit: int, offset: int
    ) -> "ReviewQueuePage":
        """Return the public view of one page of the queue."""
        return cls(
            run_id=run_id,
            items=[ReviewQueueItemView.of(item) for item in page.items],
            total=page.total,
            open_total=page.open_total,
            limit=limit,
            offset=offset,
        )


class ReviewEventCommand(BaseModel):
    """A request to append one review event.

    Every field is required except the note, and none of them names a status.
    There is no field here that could carry a new conclusion, which is the
    point: the request shape makes an override unexpressible rather than
    refusing one that was asked for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ReviewAction
    decision_fingerprint: str = Field(min_length=64, max_length=64)
    """The fingerprint served with the item the reviewer was looking at."""

    idempotency_key: str = Field(min_length=8, max_length=200)
    """The caller's key for this command. Retrying with it returns the original
    event; reusing it for a different command is refused."""

    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)


class ReviewEventReceipt(BaseModel):
    """What appending a review event produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: ReviewEventView
    workflow_state: ReviewWorkflowState
    baseline_status: DecisionStatus
    """The decision's status after the event, which is the status before it."""

    baseline_unchanged_note: str = BASELINE_IS_UNCHANGED

    @classmethod
    def of(cls, appended: AppendedReviewEvent, status: DecisionStatus) -> "ReviewEventReceipt":
        """Return the public view of an appended event."""
        return cls(
            event=ReviewEventView.of(appended.event),
            workflow_state=appended.workflow_state,
            baseline_status=status,
        )


SETTLEMENT_AND_FINALITY_ARE_SEPARATE = (
    "A settlement decision says whether the provider's own records agree. Bank "
    "finality says whether a bank statement shows the payout arriving. They are "
    "separate conclusions from separate evidence, and a line can be RESOLVED "
    "with no bank evidence at all."
)
"""The sentence every bank finality response carries.

On the wire rather than only in the interface, because a client written against
this API by somebody who never sees the screen has to be told the same thing. A
constant, so the API tests and the interface tests assert one string rather than
two that could drift apart."""


class BankFinalityAuditSummary(BaseModel):
    """One bank finality audit, without its certificates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str
    snapshot_fingerprint: str
    """The same digest the reconciliation run over these facts carries, so the
    two conclusions about one moment can be put side by side."""

    bank_finality_version: str
    bank_statement_schema_version: str
    created_at: datetime
    as_of: datetime
    fact_count: int
    payout_count: int
    bank_transaction_count: int
    outcome_counts: dict[str, int]
    verified_payout_count: int
    """How many payouts a bank statement shows arriving. Never a rate.

    A percentage here would invite somebody to read 90 percent as nearly
    settled, and the ten percent is the part a merchant is missing money in."""

    @classmethod
    def of(cls, recorded: PersistedBankFinalityAudit) -> "BankFinalityAuditSummary":
        """Return the public view of a persisted audit.

        The audit key is deliberately not exposed, for the same reason the run
        key is not: it is an idempotency identity, and publishing it would
        invite callers to depend on how it is computed.
        """
        counts = dict(sorted(recorded.outcome_counts.items()))
        return cls(
            audit_id=recorded.audit_id,
            snapshot_fingerprint=recorded.snapshot_fingerprint,
            bank_finality_version=recorded.bank_finality_version,
            bank_statement_schema_version=recorded.bank_statement_schema_version,
            created_at=recorded.created_at,
            as_of=recorded.as_of,
            fact_count=recorded.fact_count,
            payout_count=recorded.payout_count,
            bank_transaction_count=recorded.bank_transaction_count,
            outcome_counts=counts,
            verified_payout_count=counts.get(BankFinalityOutcome.VERIFIED_BANK_CREDIT.value, 0),
        )


class BankFinalityCertificateView(BaseModel):
    """One payout's bank evidence, and what it showed.

    Carries the citations and the comparison rather than a summary, so a reader
    holding the same facts can check the outcome instead of believing it.

    There is no field here called `status` and no boolean called `resolved`. The
    outcome is the outcome, and it is not the vocabulary a settlement decision
    uses: no value of `BankFinalityOutcome` is a `DecisionStatus`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    payout_id: str
    payout_source_record_id: str
    bank_reference: str | None
    """Null when the payout declared none, which is what makes it unlinkable."""

    outcome: BankFinalityOutcome
    evidence: list[EvidenceReference]
    matched_bank_transaction_ids: list[str]
    expected_amount_minor: int | None
    expected_currency: str | None
    observed_amount_minor: int | None
    observed_currency: str | None
    observed_direction: BankDirection | None
    recorded_at: datetime
    schema_version: str

    @classmethod
    def of(cls, certificate: BankFinalityCertificate) -> "BankFinalityCertificateView":
        """Return the public view of one certificate."""
        verification = {
            result.source_record_id: result.outcome for result in certificate.evidence_verification
        }
        return cls(
            payout_id=certificate.payout_id,
            payout_source_record_id=certificate.payout_source_record_id,
            bank_reference=certificate.bank_reference,
            outcome=certificate.outcome,
            evidence=[
                EvidenceReference(
                    source_record_id=reference.source_record_id,
                    source_system=reference.source_system.value,
                    payload_hash=reference.payload_hash,
                    verification_outcome=verification.get(reference.source_record_id),
                )
                for reference in certificate.evidence
            ],
            matched_bank_transaction_ids=list(certificate.matched_bank_transaction_ids),
            expected_amount_minor=certificate.expected_amount_minor,
            expected_currency=certificate.expected_currency,
            observed_amount_minor=certificate.observed_amount_minor,
            observed_currency=certificate.observed_currency,
            observed_direction=certificate.observed_direction,
            recorded_at=certificate.recorded_at,
            schema_version=certificate.schema_version,
        )


class BankFinalityAuditDetail(BaseModel):
    """An audit and the certificates it produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit: BankFinalityAuditSummary
    certificates: list[BankFinalityCertificateView]
    filtered: bool
    """True when an outcome filter narrowed the list.

    Reported so a caller cannot mistake a filtered view for the whole audit."""

    settlement_and_finality_are_separate: str = SETTLEMENT_AND_FINALITY_ARE_SEPARATE


class BankFinalityAuditPage(BaseModel):
    """A page of audit summaries, newest first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audits: list[BankFinalityAuditSummary]
    total: int
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(ge=0)
    filtered: bool
    """True when a snapshot filter narrowed the list."""

    bank_finality_version: str = BANK_FINALITY_VERSION
    settlement_and_finality_are_separate: str = SETTLEMENT_AND_FINALITY_ARE_SEPARATE


class DemoDocumentSummary(BaseModel):
    """One synthetic input document used by the public Track 04 batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_name: str
    source_record_type: SourceRecordType
    record_count: int


class DemoExceptionSummary(BaseModel):
    """One finding emitted by the real baseline over the synthetic batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    finding_count: int
    owner_lane: ClosureLane
    next_action: str
    proof_required: str
    supported_by_current_contract: bool


class DemoBatchResult(BaseModel):
    """A read-only, repeatable Track 04 reconciliation demonstration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_name: str
    seed: int
    is_synthetic: bool = True
    scenario_count: int
    source_record_count: int
    decision_count: int
    source_documents: list[DemoDocumentSummary]

    resolved_count: int
    exception_count: int
    insufficient_evidence_count: int
    auto_match_rate: Rate
    """Resolved lines over all evaluated lines; this is not an accuracy claim."""
    exception_breakdown: list[DemoExceptionSummary]

    processing_duration_ms: int
    throughput_lines_per_second: float
    """Measured over this local, temporary evaluation—not a production SLA."""

    contract_agreement: Rate
    """Strict manifest agreement: status, exact codes and exact evidence IDs."""
    exception_recall: Rate
    false_resolution_rate: Rate

    generator_version: str
    harness_version: str
    baseline_version: str
    limitation: str
