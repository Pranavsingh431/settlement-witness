"""Tests for non-authoritative evidence-request packages."""

import pytest
from pydantic import ValidationError

from app.closure.evidence_requests import (
    EVIDENCE_REQUEST_PACKAGE_VERSION,
    REQUEST_NOTICE,
    EvidenceRequestPackage,
    RequestedEvidenceReference,
    build_evidence_request,
)
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import SourceFactIndex
from app.reconciliation.batch import reconcile
from tests.reconciliation.conftest import complete_case, index_of, payout, settlement_line


def _only(index: SourceFactIndex) -> ReconciliationDecision:
    batch = reconcile(index)
    assert len(batch.decisions) == 1
    return batch.decisions[0]


class TestEvidenceRequestPackages:
    def test_package_is_a_complete_request_without_a_decision_override(self) -> None:
        decision = _only(index_of(settlement_line("sl-1"), payout("po-1")))
        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.MISSING_PAYMENT in decision.exception_codes
        before = decision.model_dump_json()

        package = build_evidence_request(decision)

        assert package.package_version == EVIDENCE_REQUEST_PACKAGE_VERSION
        assert package.decision_id == decision.decision_id
        assert package.subject_settlement_line_id == decision.subject_settlement_line_id
        assert package.baseline_status is DecisionStatus.EXCEPTION
        assert package.requested_actions[0].action_code == ExceptionCode.MISSING_PAYMENT.value
        assert package.requested_actions[0].evidence_required
        assert {reference.source_record_id for reference in package.cited_evidence} == {
            reference.source_record_id for reference in decision.evidence
        }
        assert "raw_payload" not in RequestedEvidenceReference.model_fields
        assert "new reconciliation run" in package.acceptance_condition
        assert package.request_notice == REQUEST_NOTICE
        assert "status" not in EvidenceRequestPackage.model_fields
        assert decision.model_dump_json() == before

    def test_package_keeps_all_citations_and_their_verification_outcomes(self) -> None:
        resolved = _only(complete_case())
        decision = resolved.model_copy(
            update={
                "status": DecisionStatus.EXCEPTION,
                "exception_codes": (ExceptionCode.MISSING_PAYMENT,),
            }
        )

        package = build_evidence_request(decision)

        assert [reference.source_record_id for reference in package.cited_evidence] == [
            reference.source_record_id for reference in decision.evidence
        ]
        assert [reference.verification_outcome for reference in package.cited_evidence] == [
            result.outcome for result in decision.evidence_verification
        ]

    def test_resolved_decisions_refuse_a_package(self) -> None:
        decision = _only(complete_case())

        with pytest.raises(ValueError, match="already resolved"):
            build_evidence_request(decision)

    def test_package_model_refuses_a_resolved_or_actionless_open_request(self) -> None:
        decision = _only(index_of(settlement_line("sl-1"), payout("po-1")))
        package = build_evidence_request(decision)

        with pytest.raises(ValidationError, match="resolved decision"):
            EvidenceRequestPackage(
                **{**package.model_dump(), "baseline_status": DecisionStatus.RESOLVED}
            )
        with pytest.raises(ValidationError, match="must name at least one"):
            EvidenceRequestPackage(**{**package.model_dump(), "requested_actions": ()})

    def test_package_is_immutable_and_refuses_extra_fields(self) -> None:
        decision = _only(index_of(settlement_line("sl-1"), payout("po-1")))
        package = build_evidence_request(decision)

        with pytest.raises(ValidationError, match="frozen"):
            package.primary_owner = "pretend owner"
        with pytest.raises(ValidationError, match="Extra inputs"):
            EvidenceRequestPackage(**{**package.model_dump(), "approval": "yes"})
