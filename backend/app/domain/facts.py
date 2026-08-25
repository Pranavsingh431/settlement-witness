"""Append-only source facts and the idempotency contract.

A source fact is one immutable observation of one record from one source. It is
never updated in place. Correcting an observation means recording a later fact,
so the history of what was seen and when stays intact and a decision can always
be replayed against the evidence that existed at the time.
"""

import hashlib
import json
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.primitives import (
    CanonicalPayload,
    Identifier,
    PayloadHash,
    ProviderEventId,
    SourceRecordId,
    UtcTimestamp,
)


class SourceSystem(StrEnum):
    """Where an observation came from."""

    PSP_API = "PSP_API"
    """Read from the payment service provider API."""

    PSP_WEBHOOK = "PSP_WEBHOOK"
    """Delivered by a provider webhook. Duplicates and reordering are expected."""

    BANK_STATEMENT = "BANK_STATEMENT"
    """Read from a bank statement file."""

    MERCHANT_LEDGER = "MERCHANT_LEDGER"
    """Read from the merchant's own books."""


class SourceRecordType(StrEnum):
    """What kind of record an observation holds."""

    PAYMENT_EVENT = "PAYMENT_EVENT"
    SETTLEMENT_LINE = "SETTLEMENT_LINE"
    PAYOUT = "PAYOUT"
    BANK_TRANSACTION = "BANK_TRANSACTION"


class SourceLocatorKind(StrEnum):
    """How to find the observation again in its source."""

    FILE_ROW = "FILE_ROW"
    API_RESOURCE = "API_RESOURCE"
    WEBHOOK_DELIVERY = "WEBHOOK_DELIVERY"


class SourceLocator(BaseModel):
    """Where an observation can be found again.

    This exists so that an exception can point a person at the exact row or
    resource that caused it, rather than at a record ID they then have to hunt
    for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SourceLocatorKind
    reference: Identifier
    """File path, API path or delivery identifier, depending on ``kind``."""

    row_number: int | None = None
    """One-based row number for ``FILE_ROW``. Absent for the other kinds."""

    @model_validator(mode="after")
    def _check_row_number_matches_kind(self) -> Self:
        """A row number only means something for a file row."""
        if self.kind is SourceLocatorKind.FILE_ROW:
            if self.row_number is None:
                message = "row_number is required when kind is FILE_ROW"
                raise ValueError(message)
            if self.row_number < 1:
                message = "row_number is one-based, so it must be at least 1"
                raise ValueError(message)
        elif self.row_number is not None:
            message = f"row_number is only meaningful for FILE_ROW, not {self.kind.value}"
            raise ValueError(message)
        return self


def compute_payload_hash(payload: CanonicalPayload) -> str:
    """Return the SHA-256 digest of a canonical payload.

    The payload is serialised with sorted keys, no insignificant whitespace and
    UTF-8 encoding, so that two payloads which differ only in key order or
    spacing produce the same digest. That is what makes the duplicate check in
    :func:`classify_ingestion` meaningful rather than accidental.

    Args:
        payload: The canonical payload to digest.

    Returns:
        A lowercase hexadecimal SHA-256 digest.
    """
    serialised = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


class IdempotencyKey(BaseModel):
    """The identity that decides whether two observations are the same record.

    The key is the source system plus the provider event ID. The same provider
    event ID seen through two different systems is two observations, not one,
    because the systems can disagree and that disagreement is worth keeping.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: SourceSystem
    provider_event_id: ProviderEventId


class SourceFact(BaseModel):
    """One immutable observation of one source record.

    Derived fields are validated here. ``payload_hash`` is computed by this
    system rather than supplied by the source, so a fact whose hash disagrees
    with its own payload is incoherent and is rejected at construction. Fields
    that the source declares, such as the amounts on a settlement line, are not
    second guessed here. Checking those is what the invariants are for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: SourceRecordId
    source_system: SourceSystem
    source_record_type: SourceRecordType
    source_locator: SourceLocator
    provider_event_id: ProviderEventId
    observed_at: UtcTimestamp
    """When this system saw the record. Always known."""

    occurred_at: UtcTimestamp
    """When the underlying event happened, as the source reports it."""

    payload_hash: PayloadHash
    canonical_payload: CanonicalPayload

    @model_validator(mode="after")
    def _check_payload_hash(self) -> Self:
        """Reject a fact whose declared hash does not match its payload."""
        expected = compute_payload_hash(self.canonical_payload)
        if self.payload_hash != expected:
            message = (
                "payload_hash does not match canonical_payload; "
                f"expected {expected}, got {self.payload_hash}"
            )
            raise ValueError(message)
        return self

    @property
    def idempotency_key(self) -> IdempotencyKey:
        """Return the identity used to detect duplicates."""
        return IdempotencyKey(
            source_system=self.source_system,
            provider_event_id=self.provider_event_id,
        )


class IngestionOutcome(StrEnum):
    """What a future ingestion step should do with an incoming observation."""

    NEW = "NEW"
    """No fact with this identity exists yet. Store it."""

    DUPLICATE_NO_OP = "DUPLICATE_NO_OP"
    """Same identity, same payload hash. Harmless. Store nothing and carry on."""

    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    """Same identity, different payload hash. The source contradicts itself."""


def classify_ingestion(incoming: SourceFact, existing: SourceFact | None) -> IngestionOutcome:
    """Decide what an incoming observation means next to what is already stored.

    Duplicate delivery is normal, not exceptional. A provider may send the same
    webhook twice, and a file may be loaded twice. Replaying an identical
    payload must therefore be a no-op rather than an error. The same identity
    arriving with a different payload is the case that matters, because one of
    the two observations is wrong and neither may be silently preferred.

    Args:
        incoming: The observation being offered.
        existing: The stored fact with the same idempotency key, if any.

    Returns:
        The outcome an ingestion step should act on.

    Raises:
        ValueError: If ``existing`` has a different idempotency key. Comparing
            two unrelated facts would produce a meaningless answer.
    """
    if existing is None:
        return IngestionOutcome.NEW
    if existing.idempotency_key != incoming.idempotency_key:
        message = (
            "existing fact has a different idempotency key; "
            "classify_ingestion compares observations of the same record"
        )
        raise ValueError(message)
    if existing.payload_hash == incoming.payload_hash:
        return IngestionOutcome.DUPLICATE_NO_OP
    return IngestionOutcome.DUPLICATE_CONFLICT
