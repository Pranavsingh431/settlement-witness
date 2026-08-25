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
    DecisionCandidate,
    DecisionStatus,
    check_decision_evidence,
    check_decision_invariants,
    derive_status,
    verify_decision,
)
from app.domain.evidence import EvidenceOutcome
from app.domain.facts import SourceSystem
from app.domain.invariants import (
    REQUIRED_FOR_RESOLUTION,
    InvariantId,
    InvariantOutcome,
    InvariantResult,
)
from app.domain.version import DOMAIN_SCHEMA_VERSION
from tests.domain.conftest import (
    make_candidate,
    make_decision,
    make_evidence,
    make_fact,
    make_verification,
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


class TestResolvedRequiresBacking:
    """The central rule. Each test removes exactly one pillar of a resolution."""

    def test_a_fully_backed_decision_resolves(self, resolved_decision: object) -> None:
        """The rule permits a genuine resolution."""
        assert resolved_decision.status is DecisionStatus.RESOLVED  # type: ignore[attr-defined]

    def test_resolved_without_evidence_cannot_be_built(self) -> None:
        """A resolution with nothing behind it is exactly what this forbids."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
            make_decision(evidence=(), linked_source_record_ids=())

    def test_resolved_with_a_failed_invariant_cannot_be_built(self) -> None:
        """A known break cannot be reported as a clean match."""
        with pytest.raises(ValidationError):
            make_decision(
                invariant_results=_results_with(InvariantId.INV_002, InvariantOutcome.FAILED)
            )

    def test_resolved_with_an_undetermined_invariant_cannot_be_built(self) -> None:
        """Not knowing is not the same as passing."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
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
        with pytest.raises(ValidationError, match="contradicts the backing"):
            make_decision(invariant_results=partial)

    def test_resolved_with_an_exception_code_cannot_be_built(self) -> None:
        """A decision cannot be both clean and broken."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
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
        with pytest.raises(ValidationError, match="contradicts the backing"):
            make_decision(invariant_results=extra)


class TestOtherStatuses:
    """The three non-resolved statuses each have their own obligations."""

    def test_exception_requires_a_code(self) -> None:
        """An exception with no code tells nobody anything."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
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
        with pytest.raises(ValidationError, match="contradicts the backing"):
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
        with pytest.raises(ValidationError, match="contradicts the backing"):
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
            evidence_verification=(make_verification(),),
        )
        assert status is DecisionStatus.RESOLVED

    def test_no_evidence_derives_insufficient_evidence(self) -> None:
        """Nothing cited means nothing proved."""
        status = derive_status(
            evidence=(),
            invariant_results=passing_required_results(),
            exception_codes=(),
            evidence_verification=(),
        )
        assert status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_a_missing_required_invariant_derives_insufficient_evidence(self) -> None:
        """An unrun check is unknown, not passed."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=(),
            exception_codes=(),
            evidence_verification=(make_verification(),),
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
            evidence_verification=(make_verification(),),
        )
        assert status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_a_failed_required_invariant_derives_exception(self) -> None:
        """A known break is an exception."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=_results_with(InvariantId.INV_002, InvariantOutcome.FAILED),
            exception_codes=(),
            evidence_verification=(make_verification(),),
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
            evidence=(make_evidence(),),
            invariant_results=results,
            exception_codes=(),
            evidence_verification=(make_verification(),),
        )
        assert status is DecisionStatus.EXCEPTION

    def test_timing_pending_derives_pending(self) -> None:
        """A late but plausible settlement waits."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(ExceptionCode.TIMING_PENDING,),
            evidence_verification=(make_verification(),),
        )
        assert status is DecisionStatus.PENDING

    def test_a_stronger_code_beats_timing_pending(self) -> None:
        """Lateness never masks a malformed record."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(ExceptionCode.TIMING_PENDING, ExceptionCode.MALFORMED_RECORD),
            evidence_verification=(make_verification(),),
        )
        assert status is DecisionStatus.EXCEPTION

    def test_insufficient_evidence_code_derives_its_status(self) -> None:
        """The code and the status agree."""
        status = derive_status(
            evidence=(make_evidence(),),
            invariant_results=passing_required_results(),
            exception_codes=(ExceptionCode.INSUFFICIENT_EVIDENCE,),
            evidence_verification=(make_verification(),),
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

    def test_evidence_check_catches_a_resolution_whose_citations_did_not_verify(
        self,
    ) -> None:
        """A RESOLVED decision whose certificate records a failure.

        The model refuses to construct this, because the status would contradict
        the backing. INV-006 is the layer that catches it if one arrives anyway.
        """
        smuggled = make_decision().model_construct(
            **{
                **make_decision().__dict__,
                "evidence_verification": (
                    make_verification(outcome=EvidenceOutcome.PAYLOAD_HASH_MISMATCH),
                ),
            }
        )
        result = check_decision_evidence(smuggled)
        assert result.outcome is InvariantOutcome.FAILED
        assert result.reason_code is ReasonCode.EVIDENCE_NOT_VERIFIED

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


class TestStatusIsDerivedNotAsserted:
    """The four bypasses that Phase 1 left open, each now impossible.

    Every one of these was constructible before. Each is now refused because the
    status a caller supplied contradicts the status the backing implies.
    """

    def test_exception_with_only_timing_pending_is_refused(self) -> None:
        """Lateness is not a break, so it cannot be dressed up as one."""
        with pytest.raises(ValidationError, match="implies PENDING"):
            make_decision(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.TIMING_PENDING,),
                reason_codes=(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW,),
            )

    def test_pending_without_timing_pending_is_refused(self) -> None:
        """Parking a case as pending requires an actual reason to wait."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
            make_decision(
                status=DecisionStatus.PENDING,
                exception_codes=(),
                reason_codes=(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW,),
            )

    def test_insufficient_evidence_carrying_a_stronger_code_is_refused(self) -> None:
        """A malformed record is a break, not an absence of information."""
        with pytest.raises(ValidationError, match="implies EXCEPTION"):
            make_decision(
                status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                exception_codes=(
                    ExceptionCode.INSUFFICIENT_EVIDENCE,
                    ExceptionCode.MALFORMED_RECORD,
                ),
                reason_codes=(ReasonCode.EVIDENCE_MISSING,),
            )

    def test_a_status_conflicting_with_the_highest_code_is_refused(self) -> None:
        """The highest precedence code decides, whatever the caller wrote."""
        with pytest.raises(ValidationError, match="implies INSUFFICIENT_EVIDENCE"):
            make_decision(
                status=DecisionStatus.EXCEPTION,
                exception_codes=(ExceptionCode.INSUFFICIENT_EVIDENCE,),
                reason_codes=(ReasonCode.EVIDENCE_MISSING,),
            )

    def test_resolved_cannot_be_claimed_with_unchecked_citations(self) -> None:
        """A citation with no verification result is not a verified citation."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
            make_decision(evidence_verification=())

    def test_resolved_cannot_be_claimed_when_a_citation_failed(self) -> None:
        """A certificate that records a failure cannot support a resolution."""
        with pytest.raises(ValidationError, match="contradicts the backing"):
            make_decision(
                evidence_verification=(make_verification(outcome=EvidenceOutcome.FACT_NOT_FOUND),)
            )

    def test_verification_cannot_name_a_record_the_decision_never_cited(self) -> None:
        """A certificate is about the citations made, not about arbitrary records."""
        with pytest.raises(ValidationError, match="does not cite"):
            make_decision(
                evidence_verification=(
                    make_verification(),
                    make_verification(source_record_id="rec-elsewhere"),
                )
            )

    def test_one_citation_cannot_have_two_verification_results(self) -> None:
        """Two answers would let a caller pick the flattering one."""
        with pytest.raises(ValidationError, match="more than one result for the same record"):
            make_decision(evidence_verification=(make_verification(), make_verification()))

    def test_the_same_record_cannot_be_cited_twice(self) -> None:
        """Citing one record twice would inflate an evidence count for free."""
        with pytest.raises(ValidationError, match="more than once"):
            make_decision(evidence=(make_evidence(), make_evidence()))


class TestVerifyDecisionAgainstRealFacts:
    """The factory that resolves citations and then decides the status."""

    def test_a_complete_candidate_resolves_against_a_matching_fact(self) -> None:
        """The path this whole contract exists to make trustworthy."""
        decision = verify_decision(make_candidate(), [make_fact()])

        assert decision.status is DecisionStatus.RESOLVED
        assert decision.exception_codes == ()
        assert decision.verified_evidence_count == 1
        assert all(result.is_verified for result in decision.evidence_verification)

    def test_a_citation_to_a_nonexistent_record_cannot_resolve(self) -> None:
        """The evidence is not there, so nothing can be concluded."""
        candidate = make_candidate(
            linked_source_record_ids=("rec-nope",),
            evidence=(make_evidence(source_record_id="rec-nope"),),
        )
        decision = verify_decision(candidate, [make_fact()])

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert ExceptionCode.INSUFFICIENT_EVIDENCE in decision.exception_codes
        assert ReasonCode.EVIDENCE_FACT_NOT_FOUND in decision.reason_codes

    def test_a_hash_mismatch_cannot_resolve(self) -> None:
        """The cited content is not the stored content."""
        candidate = make_candidate(evidence=(make_evidence(payload_hash="b" * 64),))
        decision = verify_decision(candidate, [make_fact()])

        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.UNMAPPED_REFERENCE in decision.exception_codes
        assert ReasonCode.EVIDENCE_PAYLOAD_HASH_MISMATCH in decision.reason_codes

    def test_a_source_system_mismatch_cannot_resolve(self) -> None:
        """The record exists but did not come from where the citation claimed."""
        candidate = make_candidate(
            evidence=(make_evidence(source_system=SourceSystem.BANK_STATEMENT),)
        )
        decision = verify_decision(candidate, [make_fact()])

        assert decision.status is DecisionStatus.EXCEPTION
        assert ExceptionCode.UNMAPPED_REFERENCE in decision.exception_codes
        assert ReasonCode.EVIDENCE_SOURCE_SYSTEM_MISMATCH in decision.reason_codes

    def test_no_facts_at_all_cannot_resolve(self) -> None:
        """An empty store proves nothing."""
        assert verify_decision(make_candidate(), []).status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_a_candidate_with_no_evidence_cannot_resolve(self) -> None:
        """Citing nothing is not a shortcut to a clean result."""
        candidate = make_candidate(evidence=(), linked_source_record_ids=())
        assert (
            verify_decision(candidate, [make_fact()]).status is DecisionStatus.INSUFFICIENT_EVIDENCE
        )

    def test_a_declared_exception_code_still_wins_over_a_clean_citation(self) -> None:
        """Verifying the evidence does not clear a break found elsewhere."""
        candidate = make_candidate(
            exception_codes=(ExceptionCode.AMOUNT_MISMATCH,),
            reason_codes=(ReasonCode.NET_FORMULA_MISMATCH,),
        )
        assert verify_decision(candidate, [make_fact()]).status is DecisionStatus.EXCEPTION

    def test_timing_pending_survives_verification(self) -> None:
        """A verified citation on a case that is merely late is still pending."""
        candidate = make_candidate(
            exception_codes=(ExceptionCode.TIMING_PENDING,),
            reason_codes=(ReasonCode.SETTLEMENT_WITHIN_EXPECTED_WINDOW,),
        )
        assert verify_decision(candidate, [make_fact()]).status is DecisionStatus.PENDING

    def test_the_factory_accepts_a_prepared_index(self) -> None:
        """Phase 2 storage will hand over an index rather than a list."""
        from app.domain.evidence import build_fact_index

        index = build_fact_index([make_fact()])
        assert verify_decision(make_candidate(), index).status is DecisionStatus.RESOLVED

    def test_a_candidate_without_reason_codes_gets_the_rule_that_fired(self) -> None:
        """Every decision says why, even when the caller offered nothing."""
        decision = verify_decision(make_candidate(reason_codes=()), [make_fact()])
        assert decision.reason_codes == (ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED,)

    def test_the_factory_is_pure(self) -> None:
        """Running it twice on the same inputs gives the same decision."""
        first = verify_decision(make_candidate(), [make_fact()])
        second = verify_decision(make_candidate(), [make_fact()])
        assert first == second

    def test_verifying_does_not_mutate_the_candidate(self) -> None:
        """The draft a caller holds is unchanged afterwards."""
        candidate = make_candidate()
        verify_decision(candidate, [make_fact()])
        assert candidate == make_candidate()


class TestDecisionCandidate:
    """A candidate is structurally validated and deliberately has no status."""

    def test_a_candidate_has_no_status_field(self) -> None:
        """Choosing a status is not a caller's job."""
        assert "status" not in DecisionCandidate.model_fields

    def test_a_candidate_has_no_verification_field(self) -> None:
        """A caller cannot supply its own certificate."""
        assert "evidence_verification" not in DecisionCandidate.model_fields

    def test_a_candidate_still_rejects_unlinked_evidence(self) -> None:
        """Structural rules apply before any fact is looked at."""
        with pytest.raises(ValidationError, match="must name a source record"):
            make_candidate(linked_source_record_ids=("rec-other",))

    def test_a_candidate_still_rejects_repeated_invariants(self) -> None:
        """One invariant, one result."""
        duplicated = (*passing_required_results(), passing_required_results()[0])
        with pytest.raises(ValidationError, match="more than one result"):
            make_candidate(invariant_results=duplicated)

    def test_a_candidate_rejects_free_text(self) -> None:
        """Model narrative has no home on a draft either."""
        with pytest.raises(ValidationError):
            make_candidate(explanation="this looks close enough")

    def test_a_candidate_is_immutable(self) -> None:
        """A draft cannot be edited while it is being verified."""
        with pytest.raises(ValidationError):
            make_candidate().decision_id = "dec-2"


class TestInv006CoversVerification:
    """INV-006 now means verified evidence, not merely cited evidence."""

    def test_a_verified_resolution_passes(self) -> None:
        """The happy path."""
        decision = verify_decision(make_candidate(), [make_fact()])
        assert check_decision_evidence(decision).outcome is InvariantOutcome.PASSED


class TestAMalformedIndexCannotResolve:
    """The decision level consequence of the source-fact index boundary."""

    def test_a_mapping_key_that_lies_cannot_produce_a_resolution(self) -> None:
        """The whole point: a lying container must not buy a clean result.

        Before this was hardened, the mapping below produced RESOLVED. The fact
        it holds matches the citation on source system and payload hash, and
        differs only in the record ID it declares about itself.
        """
        impostor = make_fact(source_record_id="different-record-id")
        reference = make_evidence(
            source_record_id="cited-record-id", payload_hash=impostor.payload_hash
        )
        candidate = make_candidate(
            linked_source_record_ids=("cited-record-id",), evidence=(reference,)
        )

        decision = verify_decision(candidate, {"cited-record-id": impostor})

        assert decision.status is not DecisionStatus.RESOLVED
        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert ReasonCode.EVIDENCE_FACT_NOT_FOUND in decision.reason_codes
        assert decision.verified_evidence_count == 0

    def test_a_well_formed_mapping_still_resolves(self) -> None:
        """The ordinary mapping path is unchanged by the hardening."""
        decision = verify_decision(make_candidate(), {"rec-1": make_fact()})
        assert decision.status is DecisionStatus.RESOLVED

    def test_a_fact_filed_under_a_wrong_key_still_resolves(self) -> None:
        """Keys are discarded, so a mislabelled fact is still the fact it is."""
        decision = verify_decision(make_candidate(), {"nonsense-key": make_fact()})
        assert decision.status is DecisionStatus.RESOLVED
