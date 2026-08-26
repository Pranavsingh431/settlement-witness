"""Persisting a reconciliation run.

A run is a statement about one snapshot of facts under one set of rule
versions. It is written once and never revised. New facts, or a new rule
version, produce a new run beside the old one.

The canonical run key is what makes re-running safe. Two reconciliations over
the same facts under the same rules are the same conclusion, so the second finds
the first rather than writing a duplicate. That is not merely an optimisation:
two rows describing the same conclusion would make the history ambiguous about
how many times something was decided.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import SourceFactIndex
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.ingestion.schemas import PARSER_VERSION
from app.reconciliation.batch import BASELINE_VERSION, ReconciliationBatch, reconcile
from app.storage.models import ReconciliationDecisionRow, ReconciliationRunRow


def compute_run_key(
    snapshot_fingerprint: str,
    *,
    baseline_version: str = BASELINE_VERSION,
    domain_schema_version: str = DOMAIN_SCHEMA_VERSION,
    parser_version: str = PARSER_VERSION,
) -> str:
    """Return the identity of a reconciliation over one snapshot under one ruleset.

    The fingerprint alone is not enough. The same facts reconciled under a newer
    baseline, a newer contract or a newer parser can reach different
    conclusions, and recording that as the same run would overwrite one answer
    with another. Every version that can change the outcome is in the key.
    """
    digest = hashlib.sha256()
    for part in (
        snapshot_fingerprint,
        baseline_version,
        domain_schema_version,
        parser_version,
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class PersistedRun(BaseModel):
    """A run as it was stored, with whether this call created it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    run_key: str
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
    was_created: bool
    """False when an existing run was returned for the same key.

    Carried so the API can answer 201 or 200 honestly rather than guessing.
    """


def _to_persisted(row: ReconciliationRunRow, *, was_created: bool) -> PersistedRun:
    """Return a stored run row as a domain level record."""
    return PersistedRun(
        run_id=row.run_id,
        run_key=row.run_key,
        snapshot_fingerprint=row.snapshot_fingerprint,
        baseline_version=row.baseline_version,
        domain_schema_version=row.domain_schema_version,
        parser_version=row.parser_version,
        created_at=_as_utc(row.created_at),
        as_of=_as_utc(row.as_of),
        fact_count=row.fact_count,
        settlement_line_count=row.settlement_line_count,
        decision_count=row.decision_count,
        status_counts=dict(row.status_counts),
        exception_counts=dict(row.exception_counts),
        was_created=was_created,
    )


def _as_utc(value: datetime) -> datetime:
    """Return a stored timestamp as an aware UTC datetime.

    SQLite has no timestamp type, so a datetime comes back without its offset.
    Everything written here was UTC before storage.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ReconciliationRunRepository:
    """Read and append reconciliation runs. There is no way to change one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_key(self, run_key: str) -> PersistedRun | None:
        """Return the run already recorded for this key, if any."""
        row = self._session.scalars(
            select(ReconciliationRunRow).where(ReconciliationRunRow.run_key == run_key)
        ).one_or_none()
        return _to_persisted(row, was_created=False) if row is not None else None

    def get(self, run_id: str) -> PersistedRun | None:
        """Return one run by its identifier."""
        row = self._session.get(ReconciliationRunRow, run_id)
        return _to_persisted(row, was_created=False) if row is not None else None

    def count(self) -> int:
        """Return how many runs are stored."""
        total = self._session.scalar(select(func.count()).select_from(ReconciliationRunRow))
        return int(total or 0)

    def list_runs(self, *, limit: int, offset: int) -> tuple[PersistedRun, ...]:
        """Return runs newest first.

        Ordered by created-at descending, then by run ID, so two runs persisted
        in the same instant still come back in a fixed order.
        """
        statement = (
            select(ReconciliationRunRow)
            .order_by(ReconciliationRunRow.created_at.desc(), ReconciliationRunRow.run_id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(
            _to_persisted(row, was_created=False) for row in self._session.scalars(statement)
        )

    def decisions_for(
        self,
        run_id: str,
        *,
        status: DecisionStatus | None = None,
        exception_code: ExceptionCode | None = None,
    ) -> tuple[ReconciliationDecision, ...]:
        """Return a run's decisions, rebuilt through the domain model.

        Every decision is revalidated on the way out. A row that was corrupted
        in the database therefore fails here rather than being served as though
        it were sound.

        Ordered by settlement line ID, matching how the baseline emits them.
        """
        statement = (
            select(ReconciliationDecisionRow)
            .where(ReconciliationDecisionRow.run_id == run_id)
            .order_by(ReconciliationDecisionRow.subject_settlement_line_id)
        )
        if status is not None:
            statement = statement.where(ReconciliationDecisionRow.status == status.value)

        rows = list(self._session.scalars(statement))
        if exception_code is not None:
            rows = [row for row in rows if exception_code.value in row.exception_codes]

        return tuple(ReconciliationDecision.model_validate(row.decision_json) for row in rows)

    def find_decision(self, run_id: str, decision_id: str) -> ReconciliationDecision | None:
        """Return one decision of one run, rebuilt through the domain model."""
        row = self._session.scalars(
            select(ReconciliationDecisionRow).where(
                ReconciliationDecisionRow.run_id == run_id,
                ReconciliationDecisionRow.decision_id == decision_id,
            )
        ).one_or_none()
        return ReconciliationDecision.model_validate(row.decision_json) if row else None

    def append(self, batch: ReconciliationBatch, run_key: str, *, now: datetime) -> PersistedRun:
        """Append a run and every decision in it.

        The caller is responsible for the transaction. Nothing here commits, so
        a failure anywhere leaves no partial run behind.
        """
        run_id = uuid4().hex
        row = ReconciliationRunRow(
            run_id=run_id,
            run_key=run_key,
            snapshot_fingerprint=batch.snapshot_fingerprint,
            baseline_version=batch.baseline_version,
            domain_schema_version=batch.domain_schema_version,
            parser_version=PARSER_VERSION,
            created_at=now,
            as_of=datetime.fromisoformat(batch.as_of),
            fact_count=batch.fact_count,
            settlement_line_count=batch.settlement_line_count,
            decision_count=len(batch.decisions),
            status_counts=dict(batch.status_counts),
            exception_counts=dict(batch.exception_counts),
        )
        self._session.add(row)
        # Flushed before the decisions, so the row their foreign key points at
        # exists. Without a mapper relationship SQLAlchemy is free to batch the
        # decision inserts ahead of the run, and SQLite rejects them.
        self._session.flush()

        self._session.add_all(_decision_rows(run_id, batch.decisions))
        self._session.flush()
        return _to_persisted(row, was_created=True)


def _decision_rows(
    run_id: str, decisions: Sequence[ReconciliationDecision]
) -> list[ReconciliationDecisionRow]:
    """Return the rows for a run's decisions.

    The complete decision is stored as canonical JSON alongside the columns that
    are queried. Storing only the columns would mean replay worked from a
    reconstruction rather than from what was decided.
    """
    return [
        ReconciliationDecisionRow(
            run_id=run_id,
            decision_id=decision.decision_id,
            subject_settlement_line_id=decision.subject_settlement_line_id,
            status=decision.status.value,
            exception_codes=[code.value for code in decision.exception_codes],
            reason_codes=[code.value for code in decision.reason_codes],
            evidence=[reference.model_dump(mode="json") for reference in decision.evidence],
            evidence_verification=[
                result.model_dump(mode="json") for result in decision.evidence_verification
            ],
            invariant_results=[
                result.model_dump(mode="json") for result in decision.invariant_results
            ],
            decision_json=decision.model_dump(mode="json"),
        )
        for decision in decisions
    ]


class ReconciliationRunService:
    """Reconciles the accepted fact store and records the result once."""

    def __init__(self, session: Session, *, now: datetime | None = None) -> None:
        """Create a service bound to one session.

        Args:
            session: The unit of work the run is written in.
            now: The wall clock time to stamp the run with. Injected so a test
                can freeze it. It is the only non-deterministic value a run
                carries, and it is metadata rather than part of the conclusion.
        """
        self._session = session
        self._repository = ReconciliationRunRepository(session)
        self._now = now or datetime.now(UTC)

    def create_run(self, index: SourceFactIndex) -> PersistedRun:
        """Reconcile the given fact index and persist the result.

        Returns the existing run when one has already been recorded for the same
        snapshot under the same rule versions, rather than writing a second row
        describing the same conclusion.

        Args:
            index: The complete accepted fact index.

        Returns:
            The run, with ``was_created`` saying whether this call wrote it.

        Raises:
            ValueError: If the index is empty. There is nothing to reconcile,
                and an empty run would look like a clean result.
        """
        batch = reconcile(index)
        run_key = compute_run_key(batch.snapshot_fingerprint)

        existing = self._repository.find_by_key(run_key)
        if existing is not None:
            return existing

        return self._repository.append(batch, run_key, now=self._now)
