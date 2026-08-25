"""Tests for the source-fact verification boundary.

These cover the gap that Phase 1 left open: a citation could name a record that
did not exist, or disagree with the record it named, and nothing checked.
"""

import pytest
from pydantic import ValidationError

from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.evidence import (
    EvidenceOutcome,
    EvidenceRef,
    EvidenceVerification,
    all_verified,
    build_fact_index,
    exception_codes_for,
    verify_against_index,
    verify_evidence,
    verify_reference,
)
from app.domain.facts import SourceSystem
from tests.domain.conftest import make_evidence, make_fact, make_verification


class TestEvidenceRefIsOnlyAPointer:
    """Evidence names an observation. It never describes one."""

    def test_a_reference_carries_only_a_pointer(self) -> None:
        """Record ID, system and payload hash, and nothing else."""
        assert set(EvidenceRef.model_fields) == {
            "source_record_id",
            "source_system",
            "payload_hash",
        }

    def test_a_reference_rejects_free_text(self) -> None:
        """There is no field through which model prose could arrive."""
        with pytest.raises(ValidationError):
            make_evidence(explanation="the model believes these match")

    def test_a_reference_rejects_a_confidence_score(self) -> None:
        """Confidence is not evidence and cannot be attached to it."""
        with pytest.raises(ValidationError):
            make_evidence(confidence=0.97)

    def test_a_reference_is_immutable(self) -> None:
        """A citation cannot be edited after a decision is made."""
        with pytest.raises(ValidationError):
            make_evidence().source_record_id = "rec-2"


class TestFactIndex:
    """The index is the read-only set of facts a citation resolves against."""

    def test_an_index_maps_record_ids_to_facts(self) -> None:
        """The normal case."""
        index = build_fact_index([make_fact()])
        assert index["rec-1"] == make_fact()

    def test_an_index_cannot_be_modified_by_its_holder(self) -> None:
        """A verifier must not be able to add the fact it is looking for."""
        index = build_fact_index([make_fact()])
        with pytest.raises(TypeError):
            index["rec-2"] = make_fact()  # type: ignore[index]

    def test_the_same_fact_twice_is_accepted(self) -> None:
        """Facts are immutable, so a repeated identical fact is harmless."""
        assert len(build_fact_index([make_fact(), make_fact()])) == 1

    def test_two_different_facts_under_one_record_id_are_refused(self) -> None:
        """Source facts are append-only, so one record ID names one fact.

        Silently picking one would hide that the caller had already lost track of
        which is authoritative.
        """
        with pytest.raises(ValueError, match="append-only"):
            build_fact_index([make_fact(), make_fact(provider_event_id="evt-changed")])

    def test_an_empty_index_is_valid(self) -> None:
        """Having no facts is a real state, not an error."""
        assert len(build_fact_index([])) == 0


class TestVerifyReference:
    """One citation against the available facts."""

    def test_a_matching_citation_verifies(self) -> None:
        """Record ID, system and hash all agree."""
        result = verify_reference(make_evidence(), [make_fact()])
        assert result.outcome is EvidenceOutcome.VERIFIED
        assert result.is_verified
        assert result.reason_code is None

    def test_a_citation_to_a_record_that_does_not_exist_fails(self) -> None:
        """The gap this boundary was added to close."""
        result = verify_reference(make_evidence(source_record_id="rec-nope"), [make_fact()])
        assert result.outcome is EvidenceOutcome.FACT_NOT_FOUND
        assert result.reason_code is ReasonCode.EVIDENCE_FACT_NOT_FOUND

    def test_a_citation_with_the_wrong_source_system_fails(self) -> None:
        """The record exists but came from somewhere else."""
        result = verify_reference(
            make_evidence(source_system=SourceSystem.BANK_STATEMENT), [make_fact()]
        )
        assert result.outcome is EvidenceOutcome.SOURCE_SYSTEM_MISMATCH
        assert result.reason_code is ReasonCode.EVIDENCE_SOURCE_SYSTEM_MISMATCH

    def test_a_citation_with_a_stale_hash_fails(self) -> None:
        """The content is not what was cited, so the citation is stale."""
        result = verify_reference(make_evidence(payload_hash="b" * 64), [make_fact()])
        assert result.outcome is EvidenceOutcome.PAYLOAD_HASH_MISMATCH
        assert result.reason_code is ReasonCode.EVIDENCE_PAYLOAD_HASH_MISMATCH

    def test_a_rewritten_fact_makes_an_earlier_citation_fail(self) -> None:
        """This is why the hash is carried on the citation at all."""
        rewritten = make_fact(canonical_payload={"amount_minor": 1})
        result = verify_reference(make_evidence(), [rewritten])
        assert result.outcome is EvidenceOutcome.PAYLOAD_HASH_MISMATCH

    def test_verification_works_against_a_prepared_index(self) -> None:
        """Callers may pass an index instead of rebuilding it per citation."""
        index = build_fact_index([make_fact()])
        assert verify_reference(make_evidence(), index).is_verified

    def test_nothing_verifies_against_no_facts(self) -> None:
        """An empty store proves nothing, and must not be treated as a pass."""
        assert not verify_reference(make_evidence(), []).is_verified


class TestVerifyEvidence:
    """Every citation, in the order it was made."""

    def test_results_come_back_in_order(self) -> None:
        """A caller can line results up with the citations that produced them."""
        evidence = (
            make_evidence(source_record_id="rec-1"),
            make_evidence(source_record_id="rec-missing"),
        )
        results = verify_evidence(evidence, [make_fact()])
        assert [result.source_record_id for result in results] == ["rec-1", "rec-missing"]

    def test_one_bad_citation_does_not_hide_the_good_ones(self) -> None:
        """Each citation is judged on its own."""
        evidence = (make_evidence(), make_evidence(source_record_id="rec-missing"))
        outcomes = [result.outcome for result in verify_evidence(evidence, [make_fact()])]
        assert outcomes == [EvidenceOutcome.VERIFIED, EvidenceOutcome.FACT_NOT_FOUND]

    def test_no_evidence_produces_no_results(self) -> None:
        """Nothing cited, nothing to check."""
        assert verify_evidence((), [make_fact()]) == ()


class TestImpliedExceptionCodes:
    """What an unresolved citation means for the decision that made it."""

    def test_a_verified_citation_implies_nothing(self) -> None:
        """A clean citation raises no code."""
        assert exception_codes_for((make_evidence(),), (make_verification(),)) == ()

    def test_a_missing_fact_implies_insufficient_evidence(self) -> None:
        """A citation resolving to nothing is an absence, not a contradiction."""
        codes = exception_codes_for(
            (make_evidence(),),
            (make_verification(outcome=EvidenceOutcome.FACT_NOT_FOUND),),
        )
        assert codes == (ExceptionCode.INSUFFICIENT_EVIDENCE,)

    def test_a_mismatch_implies_an_unmapped_reference(self) -> None:
        """The citation resolves to something other than what it claimed."""
        for outcome in (
            EvidenceOutcome.SOURCE_SYSTEM_MISMATCH,
            EvidenceOutcome.PAYLOAD_HASH_MISMATCH,
        ):
            codes = exception_codes_for((make_evidence(),), (make_verification(outcome=outcome),))
            assert codes == (ExceptionCode.UNMAPPED_REFERENCE,)

    def test_a_citation_with_no_result_counts_as_unresolved(self) -> None:
        """Skipping the check is not a way to pass it.

        Without this, a decision could avoid every evidence code simply by
        recording no verification at all.
        """
        assert exception_codes_for((make_evidence(),), ()) == (ExceptionCode.INSUFFICIENT_EVIDENCE,)

    def test_codes_are_deduplicated(self) -> None:
        """Three broken citations of the same kind raise one code."""
        evidence = tuple(make_evidence(source_record_id=f"rec-{n}") for n in range(3))
        verification = tuple(
            make_verification(source_record_id=f"rec-{n}", outcome=EvidenceOutcome.FACT_NOT_FOUND)
            for n in range(3)
        )
        assert exception_codes_for(evidence, verification) == (ExceptionCode.INSUFFICIENT_EVIDENCE,)

    def test_all_verified_agrees_with_the_implied_codes(self) -> None:
        """The convenience helper cannot drift from the rule it summarises."""
        assert all_verified((make_evidence(),), (make_verification(),))
        assert not all_verified((make_evidence(),), ())


class TestVerificationRecord:
    """The recorded result is data, never prose."""

    def test_a_verification_rejects_free_text(self) -> None:
        """No field for a narrative about why the citation was accepted."""
        with pytest.raises(ValidationError):
            EvidenceVerification(
                source_record_id="rec-1",
                outcome=EvidenceOutcome.VERIFIED,
                note="looked right to me",  # type: ignore[call-arg]
            )

    def test_a_verification_is_immutable(self) -> None:
        """A certificate cannot be edited after the fact."""
        with pytest.raises(ValidationError):
            make_verification().outcome = EvidenceOutcome.FACT_NOT_FOUND


class TestAContainerKeyIsNeverTrusted:
    """A mapping key is a label the caller chose. The fact carries its own identity.

    Before this was hardened, a mapping whose key disagreed with the fact stored
    under it produced a VERIFIED citation and a RESOLVED decision, because the
    lookup trusted the key and then checked only the system and the hash. These
    tests pin the rule that the fact, not its filing location, decides what it is.
    """

    def test_a_key_that_lies_about_its_fact_does_not_verify(self) -> None:
        """The exact shape of the original hole."""
        impostor = make_fact(source_record_id="different-record-id")
        reference = make_evidence(
            source_record_id="cited-record-id", payload_hash=impostor.payload_hash
        )

        result = verify_reference(reference, {"cited-record-id": impostor})

        assert result.outcome is not EvidenceOutcome.VERIFIED
        assert not result.is_verified

    def test_the_failure_is_reported_as_a_missing_fact(self) -> None:
        """For that citation the fact is absent, whatever else is in the mapping."""
        impostor = make_fact(source_record_id="different-record-id")
        reference = make_evidence(
            source_record_id="cited-record-id", payload_hash=impostor.payload_hash
        )

        result = verify_reference(reference, {"cited-record-id": impostor})

        assert result.outcome is EvidenceOutcome.FACT_NOT_FOUND
        assert result.reason_code is ReasonCode.EVIDENCE_FACT_NOT_FOUND

    def test_a_matching_system_and_hash_do_not_rescue_a_wrong_identity(self) -> None:
        """The two checks that used to be the only ones are not enough alone."""
        impostor = make_fact(source_record_id="different-record-id")
        reference = make_evidence(
            source_record_id="cited-record-id", payload_hash=impostor.payload_hash
        )

        assert impostor.source_system is reference.source_system
        assert impostor.payload_hash == reference.payload_hash
        assert not verify_reference(reference, {"cited-record-id": impostor}).is_verified

    def test_verify_evidence_rejects_the_same_mapping(self) -> None:
        """The batch path shares the boundary, so it inherits the rule."""
        impostor = make_fact(source_record_id="different-record-id")
        reference = make_evidence(
            source_record_id="cited-record-id", payload_hash=impostor.payload_hash
        )

        results = verify_evidence((reference,), {"cited-record-id": impostor})

        assert not all_verified((reference,), results)

    def test_the_guard_holds_even_when_handed_a_lying_index_directly(self) -> None:
        """Defence in depth.

        Normalisation means the guard inside ``verify_against_index`` is
        unreachable through the public entry points. It is kept because the
        alternative is a verifier that accepts a fact on the strength of where it
        was filed. This reaches it deliberately.
        """
        impostor = make_fact(source_record_id="different-record-id")
        reference = make_evidence(
            source_record_id="cited-record-id", payload_hash=impostor.payload_hash
        )

        result = verify_against_index(reference, {"cited-record-id": impostor})

        assert result.outcome is EvidenceOutcome.FACT_NOT_FOUND


class TestMappingsAreRebuiltFromTheirFacts:
    """Keys are discarded, so a mislabelled fact is still found by its own ID."""

    def test_a_fact_filed_under_a_wrong_key_is_found_by_its_own_id(self) -> None:
        """The fact is present, so a citation naming it correctly resolves."""
        result = verify_reference(make_evidence(), {"nonsense-key": make_fact()})
        assert result.is_verified

    def test_a_well_formed_mapping_still_works(self) -> None:
        """The ordinary case is unaffected."""
        assert verify_reference(make_evidence(), {"rec-1": make_fact()}).is_verified

    def test_an_index_from_build_fact_index_still_works(self) -> None:
        """A caller that prepared an index properly sees no change."""
        assert verify_reference(make_evidence(), build_fact_index([make_fact()])).is_verified

    def test_a_plain_iterable_still_works(self) -> None:
        """The list path is unchanged."""
        assert verify_reference(make_evidence(), [make_fact()]).is_verified

    def test_an_empty_mapping_verifies_nothing(self) -> None:
        """No facts, no verification."""
        assert not verify_reference(make_evidence(), {}).is_verified

    def test_a_mapping_holding_contradicting_facts_is_refused(self) -> None:
        """Rebuilding applies the append-only rule to a mapping's values too."""
        with pytest.raises(ValueError, match="append-only"):
            verify_reference(
                make_evidence(),
                {"a": make_fact(), "b": make_fact(provider_event_id="evt-changed")},
            )

    def test_the_same_fact_under_two_keys_is_harmless(self) -> None:
        """Duplication is not contradiction."""
        assert verify_reference(make_evidence(), {"a": make_fact(), "b": make_fact()}).is_verified
