"""Tests for the model output contract.

Most of these check that a field is absent. That reads oddly until you see what
they are for: the contract's value is what a provider cannot say through it, and
a field added later would silently make one of these claims possible again.
"""

import pytest
from pydantic import ValidationError

from app.ai.proposals import (
    MAX_SELECTED_RECORDS,
    LinkProposal,
    ProposalOutcome,
    ProviderIdentity,
    proposal_id_for,
)
from tests.ai.conftest import FIXTURE

FINGERPRINT = "a" * 64


def build(**overrides: object) -> LinkProposal:
    """Return a valid proposal unless an override makes it otherwise."""
    fields: dict[str, object] = {
        "proposal_id": "p-1",
        "subject_settlement_line_id": "line-1",
        "snapshot_fingerprint": FINGERPRINT,
        "outcome": ProposalOutcome.PROPOSE,
        "selected_source_record_ids": ("rec-1",),
        "provider": FIXTURE,
    }
    fields.update(overrides)
    return LinkProposal(**fields)  # type: ignore[arg-type]


class TestWhatAProposalMaySay:
    """The shape a provider is allowed to return."""

    def test_a_selection_is_accepted(self) -> None:
        """The ordinary case."""
        proposal = build(selected_source_record_ids=("rec-1", "rec-2"))

        assert proposal.outcome is ProposalOutcome.PROPOSE
        assert proposal.selected_source_record_ids == ("rec-1", "rec-2")

    def test_the_order_a_provider_returned_is_kept(self) -> None:
        """Not sorted on the way in.

        What the provider said is part of the record. Sorting here would make a
        duplicate at position three indistinguishable from one at position one,
        and would quietly rewrite the answer before anyone checked it.
        """
        assert build(selected_source_record_ids=("rec-9", "rec-1")).selected_source_record_ids == (
            "rec-9",
            "rec-1",
        )

    def test_an_abstention_is_accepted(self) -> None:
        """Declining is a first-class answer, not a failure."""
        proposal = build(outcome=ProposalOutcome.ABSTAIN, selected_source_record_ids=())

        assert proposal.outcome is ProposalOutcome.ABSTAIN
        assert proposal.selected_source_record_ids == ()

    def test_a_proposal_is_frozen(self) -> None:
        """What a provider said cannot be edited after the fact."""
        proposal = build()

        with pytest.raises(ValidationError):
            proposal.outcome = ProposalOutcome.ABSTAIN


class TestWhatAProposalMayNotSay:
    """The fields that do not exist, and the ones a provider cannot invent."""

    @pytest.mark.parametrize(
        "field",
        [
            "status",
            "exception_codes",
            "reason_codes",
            "invariant_results",
            "payload_hash",
            "evidence",
            "confidence",
            "explanation",
            "reasoning",
            "amount_minor",
            "currency",
            "event_type",
        ],
    )
    def test_a_forbidden_field_is_refused(self, field: str) -> None:
        """Each of these would let model output assert something.

        A status or a reason code would be a conclusion. An exception code would
        be a reported finding. A payload hash would let a provider say which
        version of a fact it saw. A confidence would invite weighing a guess
        against a deterministic check. Free text would give a justification
        somewhere to live and later be mistaken for evidence. An amount would
        let a provider restate the money.
        """
        with pytest.raises(ValidationError, match="extra"):
            build(**{field: "anything"})

    def test_the_type_declares_none_of_them(self) -> None:
        """Asserted over the schema, so a field cannot be added unnoticed."""
        declared = set(LinkProposal.model_fields)

        assert declared == {
            "proposal_id",
            "subject_settlement_line_id",
            "snapshot_fingerprint",
            "outcome",
            "selected_source_record_ids",
            "provider",
        }

    def test_there_are_exactly_two_outcomes(self) -> None:
        """No third answer, so nothing sits between proposing and declining."""
        assert {member.value for member in ProposalOutcome} == {"PROPOSE", "ABSTAIN"}


class TestTheOutcomeMustMatchTheSelection:
    """A contradiction is refused rather than resolved in the reader's head."""

    def test_an_abstention_carrying_records_is_refused(self) -> None:
        """Which half was meant is not something to guess at."""
        with pytest.raises(ValidationError, match="abstention selected 1 record"):
            build(outcome=ProposalOutcome.ABSTAIN, selected_source_record_ids=("rec-1",))

    def test_a_proposal_selecting_nothing_is_refused(self) -> None:
        """The way to say none of these is to abstain."""
        with pytest.raises(ValidationError, match="selected no records"):
            build(outcome=ProposalOutcome.PROPOSE, selected_source_record_ids=())

    def test_a_duplicate_selection_is_refused(self) -> None:
        """A set of records has no room for one twice."""
        with pytest.raises(ValidationError, match="more than once"):
            build(selected_source_record_ids=("rec-1", "rec-2", "rec-1"))

    def test_the_refusal_names_the_repeated_record(self) -> None:
        """So a shadow report says what was wrong, not that something was."""
        with pytest.raises(ValidationError, match="rec-1"):
            build(selected_source_record_ids=("rec-1", "rec-1"))

    def test_an_overlong_selection_is_refused(self) -> None:
        """A bound on cost, not a policy about linking."""
        too_many = tuple(f"rec-{index}" for index in range(MAX_SELECTED_RECORDS + 1))

        with pytest.raises(ValidationError, match="more than the 64 allowed"):
            build(selected_source_record_ids=too_many)

    def test_a_selection_at_the_limit_is_accepted(self) -> None:
        """The limit is a limit, not an off-by-one."""
        exactly = tuple(f"rec-{index}" for index in range(MAX_SELECTED_RECORDS))

        assert len(build(selected_source_record_ids=exactly).selected_source_record_ids) == 64


class TestTheFingerprintIsRequired:
    """A proposal that names no snapshot could be applied to any facts."""

    def test_a_missing_fingerprint_is_refused(self) -> None:
        """There would be nothing to check staleness against."""
        with pytest.raises(ValidationError):
            build(snapshot_fingerprint=None)

    @pytest.mark.parametrize("wrong", ["", "a" * 63, "a" * 65])
    def test_a_fingerprint_of_the_wrong_length_is_refused(self, wrong: str) -> None:
        """A digest has one length, and a truncated one would still compare."""
        with pytest.raises(ValidationError):
            build(snapshot_fingerprint=wrong)


class TestProviderIdentity:
    """Enough to tell providers and versions apart, and nothing more."""

    def test_it_declares_only_a_name_and_a_version(self) -> None:
        """Not a place for latency, tokens or a request ID to accumulate."""
        assert set(ProviderIdentity.model_fields) == {"name", "version"}

    def test_free_text_is_refused(self) -> None:
        """A note here would be provider prose attached to a claim."""
        with pytest.raises(ValidationError, match="extra"):
            ProviderIdentity(name="x", version="1", note="anything")  # type: ignore[call-arg]


class TestProposalIdentity:
    """Derived, so a shadow report is reproducible byte for byte."""

    def test_the_same_question_produces_the_same_id(self) -> None:
        """Which is what lets two runs be compared at all."""
        first = proposal_id_for(
            snapshot_fingerprint=FINGERPRINT,
            subject_settlement_line_id="line-1",
            provider=FIXTURE,
        )
        second = proposal_id_for(
            snapshot_fingerprint=FINGERPRINT,
            subject_settlement_line_id="line-1",
            provider=FIXTURE,
        )

        assert first == second

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("snapshot_fingerprint", "b" * 64),
            ("subject_settlement_line_id", "line-2"),
            ("provider", ProviderIdentity(name="fixture", version="2")),
        ],
    )
    def test_a_different_question_produces_a_different_id(self, field: str, value: object) -> None:
        """Every part of the question is in the identity."""
        base = {
            "snapshot_fingerprint": FINGERPRINT,
            "subject_settlement_line_id": "line-1",
            "provider": FIXTURE,
        }

        assert proposal_id_for(**base) != proposal_id_for(**{**base, field: value})  # type: ignore[arg-type]
