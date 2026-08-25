"""The Settlement Witness domain contract.

This package is the executable source of truth for what a fact, a lifecycle
event, an invariant, an exception and a decision mean. The Markdown in ``docs/``
explains the contract; it does not define it. Where the two ever disagree, the
code is right and the document is a bug.

Read :mod:`app.domain.decisions` first. The central rule of the whole system
lives there.
"""

from app.domain.codes import (
    EXCEPTION_PRECEDENCE,
    ExceptionCode,
    ReasonCode,
    highest_precedence,
    precedence_rank,
)
from app.domain.decisions import (
    STATUS_BY_EXCEPTION_CODE,
    DecisionCandidate,
    DecisionStatus,
    ReconciliationDecision,
    check_decision_evidence,
    check_decision_invariants,
    derive_reason_codes,
    derive_status,
    verify_decision,
)
from app.domain.evidence import (
    EvidenceOutcome,
    EvidenceRef,
    EvidenceVerification,
    SourceFactIndex,
    all_verified,
    build_fact_index,
    exception_codes_for,
    verify_against_index,
    verify_evidence,
    verify_reference,
)
from app.domain.facts import (
    IdempotencyKey,
    IngestionOutcome,
    SourceFact,
    SourceLocator,
    SourceLocatorKind,
    SourceRecordType,
    SourceSystem,
    classify_ingestion,
    compute_payload_hash,
)
from app.domain.invariants import (
    INVARIANT_CATALOGUE,
    REQUIRED_FOR_RESOLUTION,
    InvariantId,
    InvariantOutcome,
    InvariantResult,
    InvariantSpec,
    MissingInputPolicy,
    check_append_only,
    check_idempotency,
    check_money_compatibility,
    check_payout_total,
    check_returns_within_capture,
    check_settlement_line_net,
)
from app.domain.lifecycle import (
    RETURNING_EVENT_TYPES,
    PaymentEvent,
    PaymentEventType,
    PaymentIdentity,
    PayoutBatch,
    SettlementLine,
)
from app.domain.money import (
    CurrencyMismatchError,
    Money,
    MoneyBreakdown,
    compute_net_minor,
)
from app.domain.version import DOMAIN_SCHEMA_VERSION

__all__ = [
    "DOMAIN_SCHEMA_VERSION",
    "EXCEPTION_PRECEDENCE",
    "INVARIANT_CATALOGUE",
    "REQUIRED_FOR_RESOLUTION",
    "RETURNING_EVENT_TYPES",
    "STATUS_BY_EXCEPTION_CODE",
    "CurrencyMismatchError",
    "DecisionCandidate",
    "DecisionStatus",
    "EvidenceOutcome",
    "EvidenceRef",
    "EvidenceVerification",
    "ExceptionCode",
    "IdempotencyKey",
    "IngestionOutcome",
    "InvariantId",
    "InvariantOutcome",
    "InvariantResult",
    "InvariantSpec",
    "MissingInputPolicy",
    "Money",
    "MoneyBreakdown",
    "PaymentEvent",
    "PaymentEventType",
    "PaymentIdentity",
    "PayoutBatch",
    "ReasonCode",
    "ReconciliationDecision",
    "SettlementLine",
    "SourceFact",
    "SourceFactIndex",
    "SourceLocator",
    "SourceLocatorKind",
    "SourceRecordType",
    "SourceSystem",
    "all_verified",
    "build_fact_index",
    "check_append_only",
    "check_decision_evidence",
    "check_decision_invariants",
    "check_idempotency",
    "check_money_compatibility",
    "check_payout_total",
    "check_returns_within_capture",
    "check_settlement_line_net",
    "classify_ingestion",
    "compute_net_minor",
    "compute_payload_hash",
    "derive_reason_codes",
    "derive_status",
    "exception_codes_for",
    "highest_precedence",
    "precedence_rank",
    "verify_against_index",
    "verify_decision",
    "verify_evidence",
    "verify_reference",
]
