"""Tests for source-pinned, currency-safe work prioritisation."""

import pytest
from pydantic import ValidationError

from app.closure.triage import (
    CASH_TRIAGE_VERSION,
    PRIORITISATION_NOTE,
    DeclaredSettlementValue,
    Workboard,
    build_workboard,
)
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import EvidenceRef, SourceFactIndex
from app.domain.facts import SourceFact, SourceRecordType
from app.reconciliation.batch import reconcile
from tests.reconciliation.conftest import complete_case, index_of, settlement_line


def _only(index: SourceFactIndex) -> ReconciliationDecision:
    batch = reconcile(index)
    assert len(batch.decisions) == 1
    return batch.decisions[0]


def _open(
    decision: ReconciliationDecision,
    *,
    decision_id: str | None = None,
    subject: str | None = None,
    evidence: tuple[EvidenceRef, ...] | None = None,
) -> ReconciliationDecision:
    """Make a validly shaped non-resolution without changing source facts."""
    return decision.model_copy(
        update={
            "decision_id": decision_id or decision.decision_id,
            "subject_settlement_line_id": subject or decision.subject_settlement_line_id,
            "status": DecisionStatus.EXCEPTION,
            "exception_codes": (ExceptionCode.MISSING_PAYMENT,),
            "evidence": evidence or decision.evidence,
        }
    )


def _reference(fact: SourceFact, *, payload_hash: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        source_record_id=fact.source_record_id,
        source_system=fact.source_system,
        payload_hash=payload_hash or fact.payload_hash,
    )


class TestWorkboard:
    def test_open_work_is_ranked_by_absolute_declared_net_within_one_currency(self) -> None:
        base_index = complete_case()
        base = _only(base_index)
        large_inr = settlement_line(
            "sl-2", settlement_line_id="line-sl-2", payment_id="pay-2", net_minor=-200_000
        )
        usd = settlement_line(
            "sl-3",
            settlement_line_id="line-sl-3",
            payment_id="pay-3",
            net_minor=300_000,
            currency="USD",
        )
        index = index_of(*base_index.values(), large_inr, usd)
        decisions = (
            _open(base),
            _open(
                base,
                decision_id="triage:line-sl-2",
                subject="line-sl-2",
                evidence=(_reference(large_inr),),
            ),
            _open(
                base,
                decision_id="triage:line-sl-3",
                subject="line-sl-3",
                evidence=(_reference(usd),),
            ),
        )

        board = build_workboard(decisions, index)

        assert board.triage_version == CASH_TRIAGE_VERSION
        assert board.prioritisation_note == PRIORITISATION_NOTE
        assert [queue.currency for queue in board.currency_queues] == ["INR", "USD"]
        inr, usd_queue = board.currency_queues
        assert [item.subject_settlement_line_id for item in inr.items] == ["line-sl-2", "line-sl-1"]
        assert [item.rank_in_currency for item in inr.items] == [1, 2]
        assert inr.items[0].declared_settlement_value.net_minor == -200_000
        assert usd_queue.items[0].declared_settlement_value.net_minor == 300_000
        assert board.unpriced_items == ()

    def test_resolved_decisions_are_not_work_and_input_order_does_not_matter(self) -> None:
        index = complete_case()
        resolved = _only(index)
        open_decision = _open(resolved)

        first = build_workboard((resolved, open_decision), index)
        second = build_workboard((open_decision, resolved), index)

        assert len(first.currency_queues[0].items) == 1
        assert first.model_dump_json() == second.model_dump_json()

    @pytest.mark.parametrize("shape", ["missing", "hash", "record_type", "other_subject"])
    def test_unreadable_subject_evidence_stays_visible_and_unpriced(self, shape: str) -> None:
        index = complete_case()
        decision = _only(index)
        payment = next(
            fact
            for fact in index.values()
            if fact.source_record_type is SourceRecordType.PAYMENT_EVENT
        )
        settlement = next(
            fact
            for fact in index.values()
            if fact.source_record_type is SourceRecordType.SETTLEMENT_LINE
        )
        if shape == "missing":
            evidence = (
                EvidenceRef(
                    source_record_id="missing-settlement",
                    source_system=payment.source_system,
                    payload_hash=payment.payload_hash,
                ),
            )
            subject = decision.subject_settlement_line_id
        elif shape == "hash":
            evidence = (_reference(settlement, payload_hash="0" * 64),)
            subject = decision.subject_settlement_line_id
        elif shape == "record_type":
            evidence = (_reference(payment),)
            subject = decision.subject_settlement_line_id
        else:
            evidence = (_reference(settlement),)
            subject = "line-not-the-cited-line"

        board = build_workboard((_open(decision, subject=subject, evidence=evidence),), index)

        assert board.currency_queues == ()
        assert len(board.unpriced_items) == 1
        assert board.unpriced_items[0].reason == "SUBJECT_SETTLEMENT_EVIDENCE_UNAVAILABLE"

    def test_value_is_immutable_and_refuses_extra_fields(self) -> None:
        value = DeclaredSettlementValue(
            source_record_id="record",
            payload_hash="0" * 64,
            net_minor=1,
            currency="INR",
        )

        with pytest.raises(ValidationError, match="frozen"):
            value.net_minor = 2
        with pytest.raises(ValidationError, match="Extra inputs"):
            Workboard.model_validate(
                {"currency_queues": (), "unpriced_items": (), "cash_at_risk": 1}
            )
