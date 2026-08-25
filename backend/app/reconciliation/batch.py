"""The result of one reconciliation run.

Everything here is ordered, so two runs over the same facts produce byte
identical output. That is not tidiness. A result that reorders between runs
cannot be diffed, and a baseline nobody can diff is one nobody can trust to have
stayed the same when something else changed.
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import SourceFactIndex
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.reconciliation.baseline import reconcile_all
from app.reconciliation.snapshot import FactSnapshot

BASELINE_VERSION = "1.0.0"
"""Version of the matching rules.

Recorded on every batch, so a result can be traced to the rules that produced
it. It changes when a matching rule, an exception mapping, or the set of
invariants evaluated changes.
"""


class ReconciliationBatch(BaseModel):
    """One complete run over one snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_fingerprint: str
    """Identifies exactly which facts produced this result."""

    baseline_version: str
    domain_schema_version: str
    as_of: str
    """The snapshot time, as an ISO 8601 string in UTC."""

    fact_count: int
    settlement_line_count: int
    decisions: tuple[ReconciliationDecision, ...]
    """Ordered by settlement line ID."""

    status_counts: Mapping[str, int]
    exception_counts: Mapping[str, int]

    @property
    def resolved_count(self) -> int:
        """Return how many lines this run was able to resolve."""
        return self.status_counts.get(DecisionStatus.RESOLVED.value, 0)


def _tally(decisions: Sequence[ReconciliationDecision]) -> tuple[dict[str, int], dict[str, int]]:
    """Return status and exception counts, keyed in a fixed order.

    Every status appears, including the ones with no decisions. A zero that is
    printed is a zero somebody checked; a missing key is ambiguous between none
    and not measured.
    """
    statuses = {status.value: 0 for status in DecisionStatus}
    exceptions: dict[str, int] = {}

    for decision in decisions:
        statuses[decision.status.value] += 1
        for code in decision.exception_codes:
            exceptions[code.value] = exceptions.get(code.value, 0) + 1

    ordered: dict[str, int] = {
        str(code.value): exceptions[code.value]
        for code in ExceptionCode
        if code.value in exceptions
    }
    return statuses, ordered


def reconcile(index: SourceFactIndex) -> ReconciliationBatch:
    """Run the baseline over the complete accepted fact index.

    Args:
        index: The complete index, as `SourceFactRepository.fact_index` returns
            it. A partial index makes the run abstain more often. It never makes
            it resolve something it should not, because every citation is still
            verified against whatever it was given.

    Returns:
        The batch result, fully ordered.
    """
    snapshot = FactSnapshot.from_index(index)
    decisions = reconcile_all(snapshot, index)
    statuses, exceptions = _tally(decisions)

    return ReconciliationBatch(
        snapshot_fingerprint=snapshot.digest,
        baseline_version=BASELINE_VERSION,
        domain_schema_version=DOMAIN_SCHEMA_VERSION,
        as_of=snapshot.as_of.isoformat(),
        fact_count=snapshot.fact_count,
        settlement_line_count=len(snapshot.settlement_lines),
        decisions=decisions,
        status_counts=statuses,
        exception_counts=exceptions,
    )
