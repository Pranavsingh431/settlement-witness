"""Currency-safe work prioritisation derived from cited settlement evidence.

This module deliberately does not publish a cross-currency cash-at-risk total.
The only money it displays is the settlement line's own declared net, pinned to
the source fact and payload hash the decision cited. Open work is ranked by the
absolute size of that declared value *within its currency only*.

That makes the workboard useful without pretending that INR 1,000 and USD 1,000
can be added, or that a declared settlement net is a verified merchant cash
position. A bank-finality audit remains the separate conclusion about arrival.
"""

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import SourceFactIndex
from app.domain.facts import SourceRecordType
from app.ingestion.projection import project_settlement_line

CASH_TRIAGE_VERSION = "1.0.0"

PRIORITISATION_NOTE = (
    "Open work is ordered by absolute declared settlement net within each source currency. "
    "Currencies are never converted or summed. This is triage, not a cash-at-risk total."
)


class DeclaredSettlementValue(BaseModel):
    """A source-pinned settlement value for one work item.

    ``net_minor`` is the value the settlement source declared. It is not called
    verified cash, because an exception may be about that exact amount.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: str
    payload_hash: str
    net_minor: int
    currency: str


class WorkboardItem(BaseModel):
    """One unresolved decision with a comparable value in one currency queue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    subject_settlement_line_id: str
    status: DecisionStatus
    exception_codes: tuple[str, ...]
    declared_settlement_value: DeclaredSettlementValue
    rank_in_currency: int


class CurrencyWorkQueue(BaseModel):
    """Open work whose declared values share one original source currency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str
    items: tuple[WorkboardItem, ...]


class UnpricedWorkItem(BaseModel):
    """An open decision whose subject settlement fact cannot be re-read safely."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    subject_settlement_line_id: str
    status: DecisionStatus
    reason: str = "SUBJECT_SETTLEMENT_EVIDENCE_UNAVAILABLE"


class Workboard(BaseModel):
    """The deterministic, read-only prioritisation for one recorded run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    triage_version: str = CASH_TRIAGE_VERSION
    prioritisation_note: str = PRIORITISATION_NOTE
    currency_queues: tuple[CurrencyWorkQueue, ...]
    unpriced_items: tuple[UnpricedWorkItem, ...]


def _declared_settlement_value(
    decision: ReconciliationDecision, index: SourceFactIndex
) -> DeclaredSettlementValue | None:
    """Return the decision's cited subject settlement value, if still checkable.

    A current index can hold later facts, but it cannot rewrite the original
    source fact. We still require the source record ID and payload hash to match
    the evidence reference stored in the decision, so a current lookup cannot
    silently substitute a different observation for a historical conclusion.
    """
    for reference in decision.evidence:
        fact = index.get(reference.source_record_id)
        if (
            fact is None
            or fact.payload_hash != reference.payload_hash
            or fact.source_record_type is not SourceRecordType.SETTLEMENT_LINE
        ):
            continue
        line = project_settlement_line(fact)
        if line.settlement_line_id != decision.subject_settlement_line_id:
            continue
        return DeclaredSettlementValue(
            source_record_id=fact.source_record_id,
            payload_hash=fact.payload_hash,
            net_minor=line.net_minor,
            currency=line.currency,
        )
    return None


def build_workboard(
    decisions: Iterable[ReconciliationDecision], index: SourceFactIndex
) -> Workboard:
    """Prioritise unresolved decisions without combining currencies.

    The deterministic tie-breaker is the settlement line ID. This lets an
    operator compare two reads of the same immutable run without an incidental
    ordering change, even when two values have the same magnitude.
    """
    by_currency: defaultdict[str, list[WorkboardItem]] = defaultdict(list)
    unpriced: list[UnpricedWorkItem] = []

    for decision in sorted(decisions, key=lambda item: item.subject_settlement_line_id):
        if decision.status is DecisionStatus.RESOLVED:
            continue
        value = _declared_settlement_value(decision, index)
        if value is None:
            unpriced.append(
                UnpricedWorkItem(
                    decision_id=decision.decision_id,
                    subject_settlement_line_id=decision.subject_settlement_line_id,
                    status=decision.status,
                )
            )
            continue
        by_currency[value.currency].append(
            WorkboardItem(
                decision_id=decision.decision_id,
                subject_settlement_line_id=decision.subject_settlement_line_id,
                status=decision.status,
                exception_codes=tuple(code.value for code in decision.exception_codes),
                declared_settlement_value=value,
                rank_in_currency=0,
            )
        )

    queues: list[CurrencyWorkQueue] = []
    for currency, items in sorted(by_currency.items()):
        ordered = sorted(
            items,
            key=lambda item: (
                -abs(item.declared_settlement_value.net_minor),
                item.subject_settlement_line_id,
            ),
        )
        queues.append(
            CurrencyWorkQueue(
                currency=currency,
                items=tuple(
                    item.model_copy(update={"rank_in_currency": position})
                    for position, item in enumerate(ordered, start=1)
                ),
            )
        )

    return Workboard(currency_queues=tuple(queues), unpriced_items=tuple(unpriced))
