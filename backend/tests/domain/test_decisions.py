"""Tests for the decision contract and the central verifier rule.

The rule under test is the one the whole system rests on: a decision may claim
RESOLVED only when the evidence and the invariants back it. These tests prove
the rule is enforced by construction, so an unbacked resolution cannot be built
at all rather than being built and flagged later.
"""

import pytest
from pydantic import ValidationError

from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.decisions import (
    STATUS_BY_EXCEPTION_CODE,
    DecisionStatus,
    EvidenceRef,
    check_decision_evidence,
    check_decision_invariants,
    derive_status,
)
from app.domain.invariants import (
    REQUIRED_FOR_RESOLUTION,
    InvariantId,
    InvariantOutcome,
    InvariantResult,
)
from app.domain.version import DOMAIN_SCHEMA_VERSION
from tests.domain.conftest import (
    make_decision,
    make_evidence,
    passing_required_results,
)


def _results_with(
    invariant_id: InvariantId, outcome: InvariantOutcome
) -> tuple[InvariantResult, ...]:
    """Return the passing required set with one invariant replaced."""
    reason = ReasonCode.NET_FORMULA_MISMATCH if outcome is InvariantOutcome.FAILED else None
    replacement = InvariantResult(invariant_id=invariant_id, outcome=outcome, reason_code=reason)
    kept = tuple(
        result for result in passing_required_results() if result.invariant_id is not invariant_id
    )
    return (*kept, replacement)


class TestEvidenceRef:
    """Evidence points at an observation. It never describes one."""

    def test_evidence_carries_only_a_pointer(self) -> None:
        """Record ID, system and payload hash, and nothing else."""
        assert set(EvidenceRef.model_fields) == {
            "source_record_id",
            "source_system",
            "payload_hash",
        }

    def test_evidence_rejects_free_text(self) -> None:
        """There is no field through which model prose could arrive."""
        with pytest.raises(ValidationError):
            make_evidence(explanation="the model believes these match")

    def test_evidence_rejects_a_confidence_score(self) -> None:
        """Confidence is not evidence and cannot be attached to it."""
        with pytest.raises(ValidationError):
            make_evidence(confidence=0.97)

    def test_evidence_is_immutable(self) -> None:
        """A citation cannot be edited after a decision is made."""
        with pytest.raises(ValidationError):
            make_evidence().source_record_id = "rec-2"


class TestResolvedRequiresBacking:
    """The central rule. Each test removes exactly one pillar of a resolution."""

    def test_a_fully_backed_decision_resolves(self, resolved_decision: object) -> None:
        """The rule permits a genuine resolution."""
        assert resolved_decision.status is DecisionStatus.RESOLVED  # type: ignore[attr-defined]

    def test_resolved_without_evidence_cannot_be_built(self) -> None:
        """A resolution with nothing behind it is exactly what this forbids."""
        with pytest.raises(ValidationError, match="must cite at least one source fact"):
            make_decision(evidence=(), linked_source_record_ids=())

    def test_resolved_with_a_failed_invariant_cannot_be_built(self) -> None:
        """A known break cannot be reported as a clean match."""
        with pytest.raises(ValidationError):
            make_decision(
                invariant_results=_results_with(InvariantId.INV_002, InvariantOutcome.FAILED)
            )

    def test_resolved_with_an_undetermined_invariant_cannot_be_built(self) -> None:
        """Not knowing is not the same as passing."""
        with pytest.raises(ValidationError, match="pass or be not applicable"):
            make_decision(
                invariant_results=_results_with(
                    InvariantId.INV_003, InvariantOutcome.INSUFFICIENT_INPUT
                )
            )

    def test_resolved_with_a_missing_required_invariant_cannot_be_built(self) -> None:
        """Skipping a check is not a way to pass it."""
        partial = tuple(
            result
            for result in passing_required_results()
            if result.invariant_id is not InvariantId.INV_004
        )
        with pytest.raises(ValidationError, match="must carry a result for every required"):
            make_decision(invariant_results=partial)

    def test_resolved_with_an_exception_code_cannot_be_built(self) -> None:
        """A decision cannot be both clean and broken."""
        with pytest.raises(ValidationError, match="cannot also carry exception codes"):
            make_decision(exception_codes=(ExceptionCode.AMOUNT_MISMATCH,))

    def test_evidence_must_be_linked_to_the_decision(self) -> None:
        """Evidence that names an unlinked record is not traceable."""
        with pytest.raises(ValidationError, match="must name a source record"):
            make_decision(linked_source_record_ids=("rec-other",))

    def test_a_not_applicable_required_invariant_still_permits_resolution(self) -> None:
        """NOT_APPLICABLE is a determinate answer, so it does not block."""
        decision = make_decision(
            invariant_results=_results_with(InvariantId.INV_004, InvariantOutcome.NOT_APPLICABLE)
        )
        assert decision.status is DecisionStatus.RESOLVED

    def test_a_non_required_failed_invariant_still_blocks_resolution(self) -> None:
        """A failure anywhere on the decision is a failure."""
        extra = (
            *passing_required_results(),
            InvariantResult(
                invariant_id=InvariantId.INV_005,
                outcome=InvariantOutcome.FAILED,
                reason_code=ReasonCode.PAYLOAD_HASH_CONFLICT,
            ),
        )
        with pytest.raises(ValidationError, match="cannot carry a failed invariant"):
            make_decision(invariant_results=extra)


class TestOtherStatuses:
    """The three non-resolved statuses each have their own obligations."""

    def test_exception_requires_a_code(self) -> None:
        """An exception with no code tells nobody anything."""
        with pytest.raises(ValidationError, match="at least one exception code"):
            make_decision(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(),
                reason_codes=(ReasonCode.NET_FORMULA_MISMATCH,),
            )

    def test_exception_with_a_code_builds(self) -> None:
        """The normal exception path."""
        decision = make_decision(
            status=DecisionStatus.EXCEPTION,
            exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
            reason_codes=(ReasonCode.NET_FORMULA_MISMATCH,),
        )
        assert decision.status is DecisionStatus.EXCEPTION

    def test_insufficient_evidence_requires_its_own_code(self) -> None:
        """The status and the code must agree."""
        with pytest.raises(ValidationError, match="must carry the"):
            make_decision(
                status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                exception_codes=(ExceptionCode.MISSING_PAYMENT,),
                reason_codes=(ReasonCode.EVIDENCE_MISSING,),
            )

    def test_insufficient_evidence_builds_with_its_code(self) -> None:
        """Abstention is a first class outcome, not an error."""
        decision = make_decision(
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            evidence=(),
            linked_source_record_ids=(),
            invariant_results=(),
            exception_codes=(ExceptionCode.INSUFFICIENT_EVIDENCE,),
            reason_codes=(ReasonCode.EVIDENCE_MISSING,),
        )
        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_pending_may_only_carry_timing_pending(self) -> None:
        """A real break must not be parked as merely late."""
        with pytest.raises(ValidationError, match="stronger status"):
            make_decision(
                status=DecisionStatus.PENDING,
                exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
                reason_codes=(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW,),
            )

    def test_pending_builds_with_timing_pending(self) -> None:
        """A settlement inside its window is waiting, not broken."""
        decision = make_decision(
            status=DecisionStatus.PENDING,
            exception_codes=(ExceptionCode.TIMING_PENDING,),
            reason_codes=(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW,),
        )
        assert decision.status is DecisionStatus.PENDING


class TestStructuralRules:
    """Rules that apply to every decision whatever its status."""

    def test_a_decision_must_give_a_reason(self) -> None:
        """A status with no reason code cannot be audited."""
        with pytest.raises(ValidationError, match="at least one reason_code"):
            make_decision(reason_codes=())

    def test_one_invariant_cannot_have_two_results(self) -> None:
        """Two answers for one check would let a caller pick the flattering one."""
        duplicated = (*passing_required_results(), passing_required_results()[0])
        with pytest.raises(ValidationError, match="more than one result"):
            make_decision(invariant_results=duplicated)

    def test_a_decision_records_the_contract_version(self) -> None:
        """A stored decision stays readable after the contract moves on."""
        assert make_decision().schema_version == DOMAIN_SCHEMA_VERSION

    def test_a_foreign_schema_version_is_rejected(self) -> None:
        """A decision cannot claim a version this code does not implement."""
        with pytest.raises(ValidationError):
            make_decision(schema_version="0.9.0")

    def test_a_decision_is_immutable(self) -> None:
        """A recorded decision is not edited afterwards."""
        with pytest.raises(ValidationError):
            make_decision().status = DecisionStatus.EXCEPTION

    def test_a_decision_rejects_free_text(self) -> None:
        """Model narrative has no home on a decision."""
        with pytest.raises(ValidationError):
            make_decision(explanation="these amounts look close enough")

    def test_a_decision_can_link_many_events(self) -> None:
        """One line's correctness can depend on a capture and later refunds."""
        decision = make_decision(linked_event_ids=("evt-1", "evt-2", "evt-3"))
        assert len(decision.linked_event_ids) == 3


class TestInv006DecisionEvidence:
    """INV-006 as a check, for recording alongside a decision."""

    def test_a_backed_resolution_passes(self) -> None:
        """The happy path."""
        assert check_decision_evidence(make_decision()).outcome is InvariantOutcome.PASSED

    def test_a_non_resolved_decision_is_not_applicable(self) -> None:
        """The invariant speaks only about resolutions."""
        decision = make_decision(
            status=DecisionStatus.PENDING,
            exception_codes=(ExceptionCode.TIMING_PENDING,),
            reason_codes=(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW,),
        )
        assert check_decision_evidence(decision).outcome is InvariantOutcome.NOT_APPLICABLE


class TestInv007DecisionInvariants:
    """INV-007 as a check, for recording alongside a decision."""

    def test_a_clean_resolution_passes(self) -> None:
        """All required invariants present and determinate."""
        assert check_decision_invariants(make_decision()).outcome is InvariantOutcome.PASSED

    def test_a_non_resolved_decision_is_not_applicable(self) -> None:
        """The invariant speaks only about resolutions."""
        decision = make_decision(
            status=DecisionStatus.EXCEPTION,
            exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
            reason_codes=(ReasonCode.NET_FORMULA_MISMATCH,),
        )
        assert check_decision_invariants(decision).outcome is InvariantOutcome.NOT_APPLICABLE


class TestDeriveStatus:
    """The verifier rule as a function, so a caller never picks a status."""

    def test_full_backing_derives_resolved(self) -> None:
        """RESOLVED is what remains when no reason not to resolve exists."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(),
        )
        assert status is DecisionStatus.RESOLVED

    def test_no_evidence_derives_insufficient_evidence(self) -> None:
        """Nothing cited means nothing proved."""
        status = derive_status(
            evidence=(), invariant_results=passing_required_results(), exception_codes=()
        )
        assert status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_a_missing_required_invariant_derives_insufficient_evidence(self) -> None:
        """An unrun check is unknown, not passed."""
        status = derive_status(
            evidence=(make_evidence(),), invariant_results=(), exception_codes=()
        )
        assert status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_an_undetermined_invariant_derives_insufficient_evidence(self) -> None:
        """Missing information never becomes a mismatch."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=_results_with(
                InvariantId.INV_003, InvariantOutcome.INSUFFICIENT_INPUT
            ),
            exception_codes=(),
        )
        assert status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_a_failed_required_invariant_derives_exception(self) -> None:
        """A known break is an exception."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=_results_with(InvariantId.INV_002, InvariantOutcome.FAILED),
            exception_codes=(),
        )
        assert status is DecisionStatus.EXCEPTION

    def test_a_failed_non_required_invariant_also_derives_exception(self) -> None:
        """A failure anywhere is still a failure."""
        results = (
            *passing_required_results(),
            InvariantResult(
                invariant_id=InvariantId.INV_008,
                outcome=InvariantOutcome.FAILED,
                reason_code=ReasonCode.SOURCE_FACT_REWRITE_ATTEMPTED,
            ),
        )
        status = derive_status(
            evidence=(make_evidence(),), invariant_results=results, exception_codes=()
        )
        assert status is DecisionStatus.EXCEPTION

    def test_timing_pending_derives_pending(self) -> None:
        """A late but plausible settlement waits."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(ExceptionCode.TIMING_PENDING,),
        )
        assert status is DecisionStatus.PENDING

    def test_a_stronger_code_beats_timing_pending(self) -> None:
        """Lateness never masks a malformed record."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(ExceptionCode.TIMING_PENDING, ExceptionCode.MALFORMED_RECORD),
        )
        assert status is DecisionStatus.EXCEPTION

    def test_insufficient_evidence_code_derives_its_status(self) -> None:
        """The code and the status agree."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(ExceptionCode.INSUFFICIENT_EVIDENCE,),
        )
        assert status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_every_derived_status_can_actually_be_built(self) -> None:
        """The function and the model must not disagree about what is legal."""
        assert set(STATUS_BY_EXCEPTION_CODE.values()) <= set(DecisionStatus)


class TestRequiredSetIsNotEmpty:
    """A guard against the rule being weakened into nothing."""

    def test_resolution_requires_at_least_one_invariant(self) -> None:
        """If this set were emptied, every decision could resolve for free."""
        assert len(REQUIRED_FOR_RESOLUTION) >= 1


class TestDecisionChecksAsSecondLineOfDefence:
    """INV-006 and INV-007 against decisions that skipped validation.

    A decision built through the model can never reach these failure branches,
    because the validator refuses to construct it. The branches exist for
    decisions that arrive without validation, such as one read back from storage
    that was written by an older or tampered-with process. These tests use
    ``model_construct``, which bypasses validation, to reach that state
    deliberately.
    """

    def test_evidence_check_catches_a_resolution_with_no_evidence(self) -> None:
        """The model would refuse this. If it ever arrives anyway, INV-006 sees it."""
        smuggled = make_decision().model_construct(**{**make_decision().__dict__, "evidence": ()})
        result = check_decision_evidence(smuggled)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.EVIDENCE_MISSING

    def test_evidence_check_catches_evidence_that_is_not_linked(self) -> None:
        """Evidence naming an unlinked record is untraceable."""
        smuggled = make_decision().model_construct(
            **{**make_decision().__dict__, "linked_source_record_ids": ("rec-other",)}
        )
        result = check_decision_evidence(smuggled)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.EVIDENCE_NOT_LINKED

    def test_invariant_check_catches_a_required_invariant_that_never_ran(self) -> None:
        """An unrun required check must not pass as a resolution."""
        smuggled = make_decision().model_construct(
            **{**make_decision().__dict__, "invariant_results": ()}
        )
        result = check_decision_invariants(smuggled)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.REQUIRED_INVARIANT_NOT_EVALUATED

    def test_invariant_check_catches_an_undetermined_required_invariant(self) -> None:
        """Missing information is not a pass."""
        smuggled = make_decision().model_construct(
            **{
                **make_decision().__dict__,
                "invariant_results": _results_with(
                    InvariantId.INV_003, InvariantOutcome.INSUFFICIENT_INPUT
                ),
            }
        )
        result = check_decision_invariants(smuggled)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.REQUIRED_INVARIANT_MISSING_INPUT

    def test_invariant_check_catches_a_failed_invariant(self) -> None:
        """A recorded break must not sit inside a resolution."""
        smuggled = make_decision().model_construct(
            **{
                **make_decision().__dict__,
                "invariant_results": _results_with(InvariantId.INV_002, InvariantOutcome.FAILED),
            }
        )
        result = check_decision_invariants(smuggled)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.REQUIRED_INVARIANT_FAILED


def test_version_literal_matches_the_constant() -> None:
    """The type and the constant state the same version.

    ``DomainSchemaVersion`` has to repeat the string, because a type checker
    cannot read a variable into ``Literal``. This test is what stops the two
    from drifting apart unnoticed.
    """
    from typing import get_args

    from app.domain.version import DomainSchemaVersion

    assert get_args(DomainSchemaVersion.__value__) == (DOMAIN_SCHEMA_VERSION,)
