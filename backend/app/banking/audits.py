"""Persisting a bank finality audit.

An audit is a statement about one snapshot of facts under one set of bank
finality rules. It is written once and never revised.

That immutability is the point of the whole phase, not a detail of it. An audit
that reported `MISSING_BANK_EVIDENCE` was telling the truth about a moment when
the statement had not been imported. Importing it later does not make that
report wrong; it makes a new snapshot, and a new audit beside the old one. A
mutable current-status column would quietly rewrite what was known and when, and
"we did not know yet" would become unrecoverable.

The audit key is what makes re-auditing safe. The same facts under the same
rules are the same conclusion, so the second call finds the first rather than
writing a duplicate.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.banking.finality import (
    BANK_FINALITY_VERSION,
    BANK_STATEMENT_SCHEMA_VERSION,
    BankFinalityBatch,
    BankFinalityCertificate,
    BankFinalityOutcome,
    audit,
)
from app.banking.snapshot import BankFinalitySnapshot
from app.domain.evidence import SourceFactIndex
from app.storage.models import BankFinalityAuditRow, BankFinalityCertificateRow


def compute_audit_key(
    snapshot_fingerprint: str,
    *,
    bank_finality_version: str = BANK_FINALITY_VERSION,
    bank_statement_schema_version: str = BANK_STATEMENT_SCHEMA_VERSION,
) -> str:
    """Return the identity of an audit over one snapshot under one ruleset.

    Deliberately not the reconciliation run key, and deliberately not derived
    from it. The two move for different reasons: a baseline change makes a new
    run and must not make a new audit, and a bank rule change makes a new audit
    and must not make a new run. Sharing a key would tie each to the other's
    version and produce duplicates of both.
    """
    digest = hashlib.sha256()
    for part in (snapshot_fingerprint, bank_finality_version, bank_statement_schema_version):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class PersistedBankFinalityAudit(BaseModel):
    """An audit as it was stored, with whether this call created it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str
    audit_key: str
    snapshot_fingerprint: str
    bank_finality_version: str
    bank_statement_schema_version: str
    created_at: datetime
    as_of: datetime
    fact_count: int
    payout_count: int
    bank_transaction_count: int
    outcome_counts: dict[str, int]
    was_created: bool
    """False when an existing audit was returned for the same key.

    Carried so the API can answer 201 or 200 honestly rather than guessing."""


def _as_utc(value: datetime) -> datetime:
    """Return a stored timestamp as an aware UTC datetime.

    SQLite has no timestamp type, so a datetime comes back without its offset.
    Everything written here was UTC before storage.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_persisted(row: BankFinalityAuditRow, *, was_created: bool) -> PersistedBankFinalityAudit:
    """Return a stored audit row as a domain level record."""
    return PersistedBankFinalityAudit(
        audit_id=row.audit_id,
        audit_key=row.audit_key,
        snapshot_fingerprint=row.snapshot_fingerprint,
        bank_finality_version=row.bank_finality_version,
        bank_statement_schema_version=row.bank_statement_schema_version,
        created_at=_as_utc(row.created_at),
        as_of=_as_utc(row.as_of),
        fact_count=row.fact_count,
        payout_count=row.payout_count,
        bank_transaction_count=row.bank_transaction_count,
        outcome_counts=dict(row.outcome_counts),
        was_created=was_created,
    )


def _certificate_rows(
    audit_id: str, certificates: Sequence[BankFinalityCertificate]
) -> list[BankFinalityCertificateRow]:
    """Return the rows for one audit's certificates.

    The complete certificate is stored as canonical JSON alongside the columns
    that are queried, so a recomputation compares against what was concluded
    rather than against a reconstruction of it.
    """
    return [
        BankFinalityCertificateRow(
            audit_id=audit_id,
            payout_id=certificate.payout_id,
            outcome=certificate.outcome.value,
            bank_reference=certificate.bank_reference,
            matched_bank_transaction_ids=list(certificate.matched_bank_transaction_ids),
            certificate_json=certificate.model_dump(mode="json"),
        )
        for certificate in certificates
    ]


class BankFinalityAuditRepository:
    """Read and append bank finality audits. There is no way to change one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_key(self, audit_key: str) -> PersistedBankFinalityAudit | None:
        """Return the audit already recorded for this key, if any."""
        row = self._session.scalars(
            select(BankFinalityAuditRow).where(BankFinalityAuditRow.audit_key == audit_key)
        ).one_or_none()
        return _to_persisted(row, was_created=False) if row is not None else None

    def get(self, audit_id: str) -> PersistedBankFinalityAudit | None:
        """Return one audit by its identifier."""
        row = self._session.get(BankFinalityAuditRow, audit_id)
        return _to_persisted(row, was_created=False) if row is not None else None

    def count(self) -> int:
        """Return how many audits are stored."""
        total = self._session.scalar(select(func.count()).select_from(BankFinalityAuditRow))
        return int(total or 0)

    def list_audits(
        self, *, limit: int, offset: int, snapshot_fingerprint: str | None = None
    ) -> tuple[PersistedBankFinalityAudit, ...]:
        """Return audits newest first, optionally for one snapshot.

        Ordered by created-at descending, then by audit ID, so two audits
        recorded in the same instant still come back in a fixed order.
        """
        statement = select(BankFinalityAuditRow)
        if snapshot_fingerprint is not None:
            statement = statement.where(
                BankFinalityAuditRow.snapshot_fingerprint == snapshot_fingerprint
            )
        statement = (
            statement.order_by(
                BankFinalityAuditRow.created_at.desc(), BankFinalityAuditRow.audit_id
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(
            _to_persisted(row, was_created=False) for row in self._session.scalars(statement)
        )

    def count_for_snapshot(self, snapshot_fingerprint: str) -> int:
        """Return how many audits exist for one snapshot."""
        total = self._session.scalar(
            select(func.count())
            .select_from(BankFinalityAuditRow)
            .where(BankFinalityAuditRow.snapshot_fingerprint == snapshot_fingerprint)
        )
        return int(total or 0)

    def certificates_for(
        self, audit_id: str, *, outcome: BankFinalityOutcome | None = None
    ) -> tuple[BankFinalityCertificate, ...]:
        """Return an audit's certificates, rebuilt through the model.

        Every certificate is revalidated on the way out, so a row that was
        corrupted in the database fails here rather than being served as though
        it were sound.

        Ordered by payout ID, matching how the audit emits them.
        """
        statement = (
            select(BankFinalityCertificateRow)
            .where(BankFinalityCertificateRow.audit_id == audit_id)
            .order_by(BankFinalityCertificateRow.payout_id)
        )
        if outcome is not None:
            statement = statement.where(BankFinalityCertificateRow.outcome == outcome.value)
        return tuple(
            BankFinalityCertificate.model_validate(row.certificate_json)
            for row in self._session.scalars(statement)
        )

    def find_certificate(self, audit_id: str, payout_id: str) -> BankFinalityCertificate | None:
        """Return one certificate of one audit, rebuilt through the model."""
        row = self._session.scalars(
            select(BankFinalityCertificateRow).where(
                BankFinalityCertificateRow.audit_id == audit_id,
                BankFinalityCertificateRow.payout_id == payout_id,
            )
        ).one_or_none()
        return BankFinalityCertificate.model_validate(row.certificate_json) if row else None

    def append(
        self, batch: BankFinalityBatch, audit_key: str, *, now: datetime
    ) -> PersistedBankFinalityAudit:
        """Append an audit and every certificate in it.

        The caller owns the transaction. Nothing here commits, so a failure
        anywhere leaves no partial audit behind.
        """
        audit_id = uuid4().hex
        row = BankFinalityAuditRow(
            audit_id=audit_id,
            audit_key=audit_key,
            snapshot_fingerprint=batch.snapshot_fingerprint,
            bank_finality_version=batch.bank_finality_version,
            bank_statement_schema_version=batch.bank_statement_schema_version,
            created_at=now,
            as_of=batch.as_of,
            fact_count=batch.fact_count,
            payout_count=batch.payout_count,
            bank_transaction_count=batch.bank_transaction_count,
            outcome_counts=dict(batch.outcome_counts),
        )
        self._session.add(row)
        # Flushed before the certificates, so the row their foreign key points
        # at exists. Without a mapper relationship SQLAlchemy is free to batch
        # the certificate inserts ahead of the audit, and SQLite rejects them.
        self._session.flush()

        self._session.add_all(_certificate_rows(audit_id, batch.certificates))
        self._session.flush()
        return _to_persisted(row, was_created=True)


class BankFinalityAuditService:
    """Audits the accepted fact store and records the result once."""

    def __init__(self, session: Session, *, now: datetime | None = None) -> None:
        """Create a service bound to one session.

        Args:
            session: The unit of work the audit is written in.
            now: The wall clock time to record. Passed in by tests, so a
                recorded time is a fact about the test rather than about when it
                ran. It is never part of the audit key.
        """
        self._session = session
        self._repository = BankFinalityAuditRepository(session)
        self._now = now

    def create_audit(self, index: SourceFactIndex) -> PersistedBankFinalityAudit:
        """Audit every payout in the index, or return the audit already recorded.

        The lookup is the fast path and the unique constraint is the guarantee.
        Between the two there is a window in which another writer can commit the
        same audit, and the insert then fails. That is a lost race rather than a
        problem, so the savepoint is rolled back, the winner is read, and this
        caller is answered with it exactly as if the lookup had seen it.

        Only the savepoint is rolled back, so nothing partial survives: an audit
        row without its certificates would be a conclusion with no evidence
        behind it.

        Args:
            index: The complete accepted fact index.

        Returns:
            The stored audit, saying whether this call created it. A caller that
            lost the race gets `was_created` false, which is the same answer an
            ordinary duplicate gets, because it is the same fact.

        Raises:
            ValueError: If the index is empty.
            IntegrityError: If the insert failed for any reason other than
                another writer having recorded this audit. Masking that would
                turn storage corruption into a silent success.
        """
        snapshot = BankFinalitySnapshot.from_index(index)
        audit_key = compute_audit_key(snapshot.digest)

        existing = self._repository.find_by_key(audit_key)
        if existing is not None:
            return existing

        now = self._now if self._now is not None else datetime.now(UTC)
        savepoint = self._session.begin_nested()
        try:
            recorded = self._repository.append(audit(snapshot), audit_key, now=now)
        except IntegrityError:
            savepoint.rollback()
            winner = self._repository.find_by_key(audit_key)
            if winner is None:
                # Nothing is holding this key, so the constraint that refused
                # the insert was some other one. Re-raised rather than reported
                # as a duplicate.
                raise
            return winner
        savepoint.commit()
        return recorded
