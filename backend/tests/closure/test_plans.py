"""The evidence-to-closure layer routes work and never edits conclusions."""

import pytest
from pydantic import ValidationError

from app.closure.plans import (
    CLOSURE_PLAN_VERSION,
    RESOLUTION_GATE,
    ClosureAction,
    ClosureDisposition,
    ClosureLane,
    ClosurePlan,
    build_closure_plan,
    playbook_for,
)
from app.domain.codes import EXCEPTION_PRECEDENCE, ExceptionCode, ReasonCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.evidence import SourceFactIndex
from app.reconciliation.batch import reconcile
from tests.reconciliation.conftest import complete_case, index_of, payout, settlement_line


def _only(index: SourceFactIndex) -> ReconciliationDecision:
    batch = reconcile(index)
    assert len(batch.decisions) == 1
    return batch.decisions[0]


class TestPlaybooks:
    def test_every_exception_has_exactly_one_playbook(self) -> None:
        actions = [playbook_for(code)[1] for code in ExceptionCode]

        assert {action.action_code for action in actions} == {code.value for code in ExceptionCode}

    @pytest.mark.parametrize(
        ("code", "disposition", "lane", "supported"),
        [
            (
                ExceptionCode.MISSING_PAYMENT,
                ClosureDisposition.COLLECT_EVIDENCE,
                ClosureLane.EVIDENCE_OPERATIONS,
                True,
            ),
            (
                ExceptionCode.AMOUNT_MISMATCH,
                ClosureDisposition.INVESTIGATE_SOURCE,
                ClosureLane.PSP_OPERATIONS,
                False,
            ),
            (
                ExceptionCode.CURRENCY_MISMATCH,
                ClosureDisposition.ESCALATE,
                ClosureLane.FINANCE_CONTROL,
                False,
            ),
            (
                ExceptionCode.DUPLICATE_CONFLICT,
                ClosureDisposition.ESCALATE,
                ClosureLane.DATA_QUALITY,
                False,
            ),
            (
                ExceptionCode.TIMING_PENDING,
                ClosureDisposition.MONITOR,
                ClosureLane.EVIDENCE_OPERATIONS,
                True,
            ),
        ],
    )
    def test_routing_is_explicit(
        self,
        code: ExceptionCode,
        disposition: ClosureDisposition,
        lane: ClosureLane,
        supported: bool,
    ) -> None:
        actual_disposition, action = playbook_for(code)

        assert actual_disposition is disposition
        assert action.owner_lane is lane
        assert action.supported_by_current_contract is supported
        assert action.instruction
        assert action.evidence_required

    def test_an_action_is_immutable_and_refuses_extra_fields(self) -> None:
        _, action = playbook_for(ExceptionCode.MISSING_PAYMENT)

        with pytest.raises(ValidationError, match="frozen"):
            action.title = "pretend it is closed"
        with pytest.raises(ValidationError, match="Extra inputs"):
            ClosureAction(**{**action.model_dump(), "status": "RESOLVED"})


class TestPlans:
    def test_a_resolved_decision_has_no_manufactured_work(self) -> None:
        decision = _only(complete_case())

        plan = build_closure_plan(decision)

        assert plan.plan_version == CLOSURE_PLAN_VERSION
        assert plan.baseline_status is DecisionStatus.RESOLVED
        assert plan.disposition is ClosureDisposition.NO_ACTION
        assert plan.primary_owner is ClosureLane.NONE
        assert plan.blocking_codes == ()
        assert plan.actions == ()
        assert plan.requires_new_run is False
        assert "never edits this one" in plan.resolution_gate

    def test_missing_payment_becomes_a_functional_evidence_request(self) -> None:
        decision = _only(index_of(settlement_line("sl-1"), payout("po-1")))
        assert ExceptionCode.MISSING_PAYMENT in decision.exception_codes

        before = decision.model_dump_json()
        plan = build_closure_plan(decision)

        assert plan.disposition is ClosureDisposition.COLLECT_EVIDENCE
        assert plan.primary_owner is ClosureLane.EVIDENCE_OPERATIONS
        assert ExceptionCode.MISSING_PAYMENT.value in plan.blocking_codes
        assert plan.actions[0].action_code == ExceptionCode.MISSING_PAYMENT.value
        assert "PAYMENT_EVENT" in plan.actions[0].evidence_required
        assert plan.requires_new_run is True
        assert plan.resolution_gate == RESOLUTION_GATE
        assert decision.model_dump_json() == before

    def test_multiple_findings_are_all_kept_in_contract_precedence(self) -> None:
        resolved = _only(complete_case())
        codes = (
            ExceptionCode.PARTIAL_REFUND,
            ExceptionCode.AMOUNT_MISMATCH,
            ExceptionCode.CURRENCY_MISMATCH,
        )
        synthetic = resolved.model_copy(
            update={"status": DecisionStatus.EXCEPTION, "exception_codes": codes}
        )

        plan = build_closure_plan(synthetic)

        expected = tuple(code for code in EXCEPTION_PRECEDENCE if code in codes)
        assert plan.blocking_codes == tuple(code.value for code in expected)
        assert tuple(action.action_code for action in plan.actions) == plan.blocking_codes
        assert plan.headline == plan.actions[0].title

    def test_a_code_free_unresolved_certificate_is_escalated_loudly(self) -> None:
        resolved = _only(complete_case())
        synthetic = resolved.model_copy(
            update={
                "status": DecisionStatus.INSUFFICIENT_EVIDENCE,
                "exception_codes": (),
                "reason_codes": (ReasonCode.REQUIRED_INVARIANT_MISSING_INPUT,),
            }
        )

        plan = build_closure_plan(synthetic)

        assert plan.disposition is ClosureDisposition.ESCALATE
        assert plan.blocking_codes == (ReasonCode.REQUIRED_INVARIANT_MISSING_INPUT.value,)
        assert plan.actions[0].action_code == "UNRESOLVED_CERTIFICATE"
        assert (
            ReasonCode.REQUIRED_INVARIANT_MISSING_INPUT.value in plan.actions[0].evidence_required
        )

    def test_a_plan_is_immutable_and_has_no_decision_override_field(self) -> None:
        plan = build_closure_plan(_only(complete_case()))

        assert "status" not in ClosurePlan.model_fields
        assert "baseline_status" in ClosurePlan.model_fields
        with pytest.raises(ValidationError, match="frozen"):
            plan.headline = "changed"

    @pytest.mark.parametrize(
        "fields",
        [
            {
                "baseline_status": DecisionStatus.RESOLVED,
                "disposition": ClosureDisposition.ESCALATE,
                "primary_owner": ClosureLane.FINANCE_CONTROL,
                "headline": "wrong",
                "blocking_codes": ("WRONG",),
                "actions": (
                    ClosureAction(
                        action_code="WRONG",
                        owner_lane=ClosureLane.FINANCE_CONTROL,
                        title="wrong",
                        instruction="wrong",
                        evidence_required="wrong",
                        supported_by_current_contract=False,
                    ),
                ),
                "requires_new_run": True,
                "resolution_gate": "wrong",
            },
            {
                "baseline_status": DecisionStatus.EXCEPTION,
                "disposition": ClosureDisposition.NO_ACTION,
                "primary_owner": ClosureLane.NONE,
                "headline": "wrong",
                "blocking_codes": (),
                "actions": (),
                "requires_new_run": False,
                "resolution_gate": "wrong",
            },
        ],
    )
    def test_a_plan_cannot_contradict_its_baseline_status(self, fields: dict[str, object]) -> None:
        with pytest.raises(ValidationError, match="closure work"):
            ClosurePlan(**fields)  # type: ignore[arg-type]
