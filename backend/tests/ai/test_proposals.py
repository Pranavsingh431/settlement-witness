"""Tests for the model output contract and the envelope built around it.

Two layers, tested as two things. `RawLinkSelection` is what a provider may
return, and most of these tests check that a field is absent from it. That reads
oddly until you see what they are for: the contract's value is what a provider
cannot say through it.

`LinkProposal` is the envelope the server builds. Its tests check the opposite
property, that every field except the selection came from the caller's own
knowledge rather than from the response.
"""

import pytest
from pydantic import ValidationError

from app.ai.proposals import (
    MAX_SELECTED_RECORDS,
    LinkProposal,
    ProposalOutcome,
    ProviderIdentity,
    RawLinkSelection,
    bind,
    proposal_id_for,
)
from tests.ai.conftest import FIXTURE

FINGERPRINT = "a" * 64
ENVIRONMENT = "e" * 64
REQUEST = "r" * 64


def selection(**overrides: object) -> RawLinkSelection:
    """Return a valid raw selection unless an override makes it otherwise."""
    fields: dict[str, object] = {
        "outcome": ProposalOutcome.PROPOSE,
        "selected_source_record_ids": ("rec-1",),
    }
    fields.update(overrides)
    return RawLinkSelection(**fields)  # type: ignore[arg-type]


class TestWhatAProviderMaySay:
    """The two fields, and the shapes they may take."""

    def test_a_selection_is_accepted(self) -> None:
        """The ordinary case."""
        raw = selection(selected_source_record_ids=("rec-1", "rec-2"))

        assert raw.outcome is ProposalOutcome.PROPOSE
        assert raw.selected_source_record_ids == ("rec-1", "rec-2")

    def test_the_order_a_provider_returned_is_kept(self) -> None:
        """Not sorted on the way in.

        What the provider said is part of the record. Sorting here would make a
        duplicate at position three indistinguishable from one at position one,
        and would quietly rewrite the answer before anyone checked it.
        """
        assert selection(
            selected_source_record_ids=("rec-9", "rec-1")
        ).selected_source_record_ids == (
            "rec-9",
            "rec-1",
        )

    def test_an_abstention_is_accepted(self) -> None:
        """Declining is a first-class answer, not a failure."""
        raw = selection(outcome=ProposalOutcome.ABSTAIN, selected_source_record_ids=())

        assert raw.outcome is ProposalOutcome.ABSTAIN
        assert raw.selected_source_record_ids == ()

    def test_a_selection_is_frozen(self) -> None:
        """What a provider said cannot be edited after the fact."""
        raw = selection()

        with pytest.raises(ValidationError):
            raw.outcome = ProposalOutcome.ABSTAIN

    def test_it_declares_exactly_two_fields(self) -> None:
        """Asserted over the schema, so a third cannot be added unnoticed."""
        assert set(RawLinkSelection.model_fields) == {"outcome", "selected_source_record_ids"}

    def test_there_are_exactly_two_outcomes(self) -> None:
        """No third answer, so nothing sits between proposing and declining."""
        assert {member.value for member in ProposalOutcome} == {"PROPOSE", "ABSTAIN"}


class TestWhatAProviderMayNotSay:
    """The fields that do not exist, in two groups.

    The first group would let model output assert something. The second is
    metadata that has a correct value the provider does not own: which line was
    asked about, which snapshot, and who is answering are all things the caller
    knew before it called anything.
    """

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
    def test_an_asserting_field_is_refused(self, field: str) -> None:
        """Each of these would let model output claim something.

        A status or a reason code would be a conclusion. An exception code would
        be a reported finding. A payload hash would let a provider say which
        version of a fact it saw. A confidence would invite weighing a guess
        against a deterministic check. Free text would give a justification
        somewhere to live and later be mistaken for evidence. An amount would
        let a provider restate the money.
        """
        with pytest.raises(ValidationError, match="extra"):
            selection(**{field: "anything"})

    @pytest.mark.parametrize(
        "field",
        [
            "proposal_id",
            "provider",
            "subject_settlement_line_id",
            "snapshot_fingerprint",
            "page_ordinal",
            "environment_fingerprint",
            "request_fingerprint",
        ],
    )
    def test_a_metadata_field_is_refused(self, field: str) -> None:
        """Refused even though a correct value exists.

        A provider supplying its own identity could sign an answer as another
        provider. One supplying a subject or a fingerprint could answer about a
        different line or a different set of facts. And one choosing a proposal
        ID could decide what its answer is filed under. None of it is the
        provider's to give, so none of it has a field.
        """
        with pytest.raises(ValidationError, match="extra"):
            selection(**{field: "anything"})

    def test_a_forged_identity_cannot_be_smuggled_in(self) -> None:
        """The concrete attack, written out.

        Before this was split into two layers, a response carrying
        `provider: {name: "attacker"}` and an arbitrary ID validated, and the
        forged values were what got recorded.
        """
        with pytest.raises(ValidationError, match="extra"):
            RawLinkSelection.model_validate(
                {
                    "outcome": "PROPOSE",
                    "selected_source_record_ids": ["rec-1"],
                    "provider": {"name": "attacker", "version": "999"},
                    "proposal_id": "anything-i-like",
                }
            )


class TestTheOutcomeMustMatchTheSelection:
    """A contradiction is refused rather than resolved in the reader's head."""

    def test_an_abstention_carrying_records_is_refused(self) -> None:
        """Which half was meant is not something to guess at."""
        with pytest.raises(ValidationError, match="abstention selected 1 record"):
            selection(outcome=ProposalOutcome.ABSTAIN, selected_source_record_ids=("rec-1",))

    def test_a_proposal_selecting_nothing_is_refused(self) -> None:
        """The way to say none of these is to abstain."""
        with pytest.raises(ValidationError, match="selected no records"):
            selection(outcome=ProposalOutcome.PROPOSE, selected_source_record_ids=())

    def test_a_duplicate_selection_is_refused(self) -> None:
        """A set of records has no room for one twice."""
        with pytest.raises(ValidationError, match="more than once"):
            selection(selected_source_record_ids=("rec-1", "rec-2", "rec-1"))

    def test_the_refusal_names_the_repeated_record(self) -> None:
        """So a shadow report says what was wrong, not that something was."""
        with pytest.raises(ValidationError, match="rec-1"):
            selection(selected_source_record_ids=("rec-1", "rec-1"))

    def test_an_overlong_selection_is_refused(self) -> None:
        """A bound on cost, not a policy about linking."""
        too_many = tuple(f"rec-{index}" for index in range(MAX_SELECTED_RECORDS + 1))

        with pytest.raises(ValidationError, match="more than the 64 allowed"):
            selection(selected_source_record_ids=too_many)

    def test_a_selection_at_the_limit_is_accepted(self) -> None:
        """The limit is a limit, not an off-by-one."""
        exactly = tuple(f"rec-{index}" for index in range(MAX_SELECTED_RECORDS))

        assert len(selection(selected_source_record_ids=exactly).selected_source_record_ids) == 64


class TestBindingIsWhereMetadataComesFrom:
    """Every field but the selection is written by the server."""

    def test_the_subject_and_snapshot_come_from_the_caller(self) -> None:
        """Not from the response, which carries neither."""
        proposal = bind(
            selection(),
            subject_settlement_line_id="line-7",
            snapshot_fingerprint=FINGERPRINT,
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=FIXTURE,
        )

        assert proposal.subject_settlement_line_id == "line-7"
        assert proposal.snapshot_fingerprint == FINGERPRINT

    def test_the_provider_identity_comes_from_the_provider_object(self) -> None:
        """Whatever the response said, which is nothing."""
        real = ProviderIdentity(name="the-real-provider", version="7")

        proposal = bind(
            selection(),
            subject_settlement_line_id="line-7",
            snapshot_fingerprint=FINGERPRINT,
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=real,
        )

        assert proposal.provider == real

    def test_the_proposal_id_is_derived_not_accepted(self) -> None:
        """So a provider cannot choose what its answer is filed under."""
        real = ProviderIdentity(name="custom", version="3")

        proposal = bind(
            selection(),
            subject_settlement_line_id="line-7",
            snapshot_fingerprint=FINGERPRINT,
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=real,
        )

        assert proposal.proposal_id == proposal_id_for(
            snapshot_fingerprint=FINGERPRINT,
            subject_settlement_line_id="line-7",
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=real,
        )

    def test_the_selection_is_carried_through_unchanged(self) -> None:
        """The one part that is the provider's."""
        raw = selection(selected_source_record_ids=("rec-9", "rec-1"))

        proposal = bind(
            raw,
            subject_settlement_line_id="line-7",
            snapshot_fingerprint=FINGERPRINT,
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=FIXTURE,
        )

        assert proposal.selected_source_record_ids == ("rec-9", "rec-1")
        assert proposal.outcome is raw.outcome

    def test_two_providers_bind_the_same_selection_differently(self) -> None:
        """The identity is part of what a proposal is filed under."""
        raw = selection()
        first = bind(
            raw,
            subject_settlement_line_id="line-7",
            snapshot_fingerprint=FINGERPRINT,
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=ProviderIdentity(name="one", version="1"),
        )
        second = bind(
            raw,
            subject_settlement_line_id="line-7",
            snapshot_fingerprint=FINGERPRINT,
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=ProviderIdentity(name="two", version="1"),
        )

        assert first.proposal_id != second.proposal_id
        assert first.provider != second.provider

    def test_the_envelope_declares_what_it_should(self) -> None:
        """Asserted over the schema, so a field cannot appear unnoticed."""
        assert set(LinkProposal.model_fields) == {
            "proposal_id",
            "subject_settlement_line_id",
            "snapshot_fingerprint",
            "environment_fingerprint",
            "page_ordinal",
            "request_fingerprint",
            "outcome",
            "selected_source_record_ids",
            "provider",
        }

    def test_the_envelope_carries_no_asserting_field(self) -> None:
        """The server builds it, and the server has nothing to assert either."""
        declared = set(LinkProposal.model_fields)

        for forbidden in ("status", "exception_codes", "reason_codes", "payload_hash"):
            assert forbidden not in declared


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
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=FIXTURE,
        )
        second = proposal_id_for(
            snapshot_fingerprint=FINGERPRINT,
            subject_settlement_line_id="line-1",
            environment_fingerprint=ENVIRONMENT,
            page_ordinal=1,
            request_fingerprint=REQUEST,
            provider=FIXTURE,
        )

        assert first == second

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("snapshot_fingerprint", "b" * 64),
            ("subject_settlement_line_id", "line-2"),
            ("environment_fingerprint", "f" * 64),
            ("page_ordinal", 2),
            ("request_fingerprint", "9" * 64),
            ("provider", ProviderIdentity(name="fixture", version="2")),
        ],
    )
    def test_a_different_question_produces_a_different_id(self, field: str, value: object) -> None:
        """Every part of the question is in the identity."""
        base = {
            "snapshot_fingerprint": FINGERPRINT,
            "subject_settlement_line_id": "line-1",
            "environment_fingerprint": ENVIRONMENT,
            "page_ordinal": 1,
            "request_fingerprint": REQUEST,
            "provider": FIXTURE,
        }

        assert proposal_id_for(**base) != proposal_id_for(**{**base, field: value})  # type: ignore[arg-type]


class TestTheEnvelopeChecksItselfToo:
    """The same rules again, on the type that gets handed onward.

    `bind` only ever builds an envelope from a selection that already passed
    these, so nothing in the normal path reaches them. They are tested by
    constructing the envelope directly, which is what a future caller that
    forgot to go through `bind` would do.
    """

    def envelope(self, **overrides: object) -> LinkProposal:
        """Return an envelope built directly, bypassing the binder."""
        fields: dict[str, object] = {
            "proposal_id": "p-1",
            "subject_settlement_line_id": "line-1",
            "snapshot_fingerprint": FINGERPRINT,
            "environment_fingerprint": ENVIRONMENT,
            "page_ordinal": 1,
            "request_fingerprint": REQUEST,
            "outcome": ProposalOutcome.PROPOSE,
            "selected_source_record_ids": ("rec-1",),
            "provider": FIXTURE,
        }
        fields.update(overrides)
        return LinkProposal(**fields)  # type: ignore[arg-type]

    def test_a_well_formed_envelope_is_accepted(self) -> None:
        """The baseline the rest of these change one field of."""
        assert self.envelope().outcome is ProposalOutcome.PROPOSE

    def test_an_abstention_carrying_records_is_refused(self) -> None:
        """Caught here as well as at the raw layer."""
        with pytest.raises(ValidationError, match="abstention selected 1 record"):
            self.envelope(outcome=ProposalOutcome.ABSTAIN)

    def test_a_proposal_carrying_no_records_is_refused(self) -> None:
        """The other half of the same contradiction."""
        with pytest.raises(ValidationError, match="selected no records"):
            self.envelope(selected_source_record_ids=())

    def test_a_duplicate_selection_is_refused(self) -> None:
        """A set of records has no room for one twice."""
        with pytest.raises(ValidationError, match="more than once"):
            self.envelope(selected_source_record_ids=("rec-1", "rec-1"))

    def test_an_overlong_selection_is_refused(self) -> None:
        """The bound applies to whatever is handed onward, not only to input."""
        too_many = tuple(f"rec-{index}" for index in range(MAX_SELECTED_RECORDS + 1))

        with pytest.raises(ValidationError, match="more than the 64 allowed"):
            self.envelope(selected_source_record_ids=too_many)

    def test_an_abstaining_envelope_with_no_records_is_accepted(self) -> None:
        """The coherent abstention, so the rule is not simply refusing both."""
        proposal = self.envelope(outcome=ProposalOutcome.ABSTAIN, selected_source_record_ids=())

        assert proposal.selected_source_record_ids == ()

    @pytest.mark.parametrize("wrong", ["", "a" * 63, "a" * 65])
    def test_a_fingerprint_of_the_wrong_length_is_refused(self, wrong: str) -> None:
        """A digest has one length, and a truncated one would still compare."""
        with pytest.raises(ValidationError):
            self.envelope(snapshot_fingerprint=wrong)
