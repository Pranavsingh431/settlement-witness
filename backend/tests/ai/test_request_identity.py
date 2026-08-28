"""Tests for identity that reflects what the provider was actually shown.

The defect these exist for: the environment fingerprint identifies which records
a universe holds, and three renderings of one universe carry the same value. So
a run over canonical references and a run over withheld ones produced the same
proposal IDs and reports that looked directly comparable, when they were answers
to different questions.

The request fingerprint is the other half of the identity. It says what was
shown, and the two together say which records and how.
"""

import pytest

from app.ai.candidates import build_pages, build_requests
from app.ai.evaluation import evaluate, request_set_fingerprint
from app.ai.presentation import ReferenceStyle
from app.ai.provider import FixtureProvider, always_abstains, selects_everything
from app.ai.validation import ValidProposal, parse_proposal
from app.reconciliation.snapshot import FactSnapshot
from tests.ai.conftest import FIXTURE, payload_for
from tests.ai.test_safe_abstention import ABSTAIN_EXPECTED  # noqa: F401
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line


@pytest.fixture
def snapshot() -> FactSnapshot:
    """Return a small snapshot with one line and two candidates."""
    return FactSnapshot.from_index(
        index_of(payment_event("pe-1", payment_id="pay-1"), settlement_line("sl-1"), payout("po-1"))
    )


def styling(snapshot: FactSnapshot, style: ReferenceStyle) -> dict[str, ReferenceStyle]:
    """Return a styling applying one style to every record."""
    return dict.fromkeys(snapshot.facts_by_record_id, style)


PRESENTATIONS = ["canonical", "truncated", "withheld", "near miss", "underscored"]


def stylings(snapshot: FactSnapshot) -> dict[str, dict[str, ReferenceStyle]]:
    """Return one styling per presentation worth telling apart."""
    return {
        "canonical": {},
        "truncated": styling(snapshot, ReferenceStyle.TRUNCATED),
        "withheld": styling(snapshot, ReferenceStyle.WITHHELD),
        "near miss": styling(snapshot, ReferenceStyle.NEAR_MISS),
        "underscored": styling(snapshot, ReferenceStyle.UNDERSCORED),
    }


class TestTheRequestFingerprintTracksWhatWasShown:
    """Different presentations of one universe are different questions."""

    def test_every_presentation_gets_its_own_fingerprint(self, snapshot: FactSnapshot) -> None:
        """The reproduction, kept as a test.

        Before this phase all five shared one identity, because the environment
        fingerprint describes the records and not the rendering.
        """
        seen = {
            label: build_pages("line-sl-1", snapshot, style)[0].request_fingerprint
            for label, style in stylings(snapshot).items()
        }

        assert len(set(seen.values())) == len(PRESENTATIONS)

    def test_the_environment_fingerprint_stays_the_same(self, snapshot: FactSnapshot) -> None:
        """Because the universe is the same universe.

        The two answer different questions and both are worth having: which
        records, and what was shown about them.
        """
        seen = {
            build_pages("line-sl-1", snapshot, style)[0].environment_fingerprint
            for style in stylings(snapshot).values()
        }

        assert len(seen) == 1

    def test_rebuilding_the_same_request_is_identical(self, snapshot: FactSnapshot) -> None:
        """Byte for byte, or nothing downstream is reproducible."""
        style = styling(snapshot, ReferenceStyle.TRUNCATED)

        first = build_pages("line-sl-1", snapshot, style)[0]
        second = build_pages("line-sl-1", snapshot, style)[0]

        assert first.request_fingerprint == second.request_fingerprint
        assert first.model_dump_json() == second.model_dump_json()

    def test_a_withheld_field_is_not_the_same_as_an_empty_one(self, snapshot: FactSnapshot) -> None:
        """The absence marker.

        Without it a reference that was withheld and one that rendered as an
        empty string would contribute the same bytes, and two different
        questions would share a fingerprint. Built directly, because the domain
        refuses a payment event with an empty payment ID and so a snapshot
        cannot produce this pair.
        """
        page = build_pages("line-sl-1", snapshot)[0]

        withheld = page.model_copy(update={"subject_payout_id": None})
        empty = page.model_copy(update={"subject_payout_id": ""})

        assert withheld.request_fingerprint != empty.request_fingerprint

    def test_it_carries_nothing_private(self, snapshot: FactSnapshot) -> None:
        """It is built from the request, and a request holds nothing private.

        Stated as a property of its inputs rather than by scanning a digest,
        which would prove nothing either way.
        """
        page = build_pages("line-sl-1", snapshot)[0]
        rendered = page.model_dump_json()

        for private in ("expected_action", "ABSTAIN", "linked_record_ids", "family"):
            assert private not in rendered


class TestProposalIdentityIncludesThePresentation:
    """The same page under two renderings is two records, not one."""

    def test_two_presentations_produce_different_proposal_ids(self, snapshot: FactSnapshot) -> None:
        """Same provider, line, snapshot and page. Different question."""
        ids = set()
        for style in stylings(snapshot).values():
            page = build_pages("line-sl-1", snapshot, style)[0]
            result = parse_proposal(payload_for(()), page, FIXTURE)
            assert isinstance(result, ValidProposal)
            ids.add(result.proposal.proposal_id)

        assert len(ids) == len(PRESENTATIONS)

    def test_the_same_selection_under_two_presentations_stays_distinct(
        self, snapshot: FactSnapshot
    ) -> None:
        """A provider that named the same records answered two questions.

        The selection is identical and the proposals are not, because what it
        was shown differed and the record of what it did should say so.
        """
        canonical = build_pages("line-sl-1", snapshot)[0]
        hidden = build_pages("line-sl-1", snapshot, styling(snapshot, ReferenceStyle.WITHHELD))[0]
        chosen = (sorted(canonical.candidate_ids)[0],)

        first = parse_proposal(payload_for(chosen), canonical, FIXTURE)
        second = parse_proposal(payload_for(chosen), hidden, FIXTURE)
        assert isinstance(first, ValidProposal)
        assert isinstance(second, ValidProposal)

        assert (
            first.proposal.selected_source_record_ids == second.proposal.selected_source_record_ids
        )
        assert first.proposal.proposal_id != second.proposal.proposal_id
        assert first.proposal.request_fingerprint != second.proposal.request_fingerprint


class TestReportIdentityIncludesThePresentation:
    """Two reports over one snapshot are comparable only if the questions were."""

    def test_every_presentation_gets_its_own_request_set_fingerprint(
        self, snapshot: FactSnapshot
    ) -> None:
        """So a reader can see that two reports are not two attempts at one thing."""
        seen = {
            evaluate(
                snapshot, FixtureProvider(always_abstains()), None, style
            ).request_set_fingerprint
            for style in stylings(snapshot).values()
        }

        assert len(seen) == len(PRESENTATIONS)

    def test_the_same_presentation_gives_the_same_fingerprint(self, snapshot: FactSnapshot) -> None:
        """Reproducibility, which is the other half of the point."""
        style = styling(snapshot, ReferenceStyle.UNDERSCORED)

        first = evaluate(snapshot, FixtureProvider(always_abstains()), None, style)
        second = evaluate(snapshot, FixtureProvider(always_abstains()), None, style)

        assert first.request_set_fingerprint == second.request_set_fingerprint
        assert first.model_dump_json() == second.model_dump_json()

    def test_it_does_not_depend_on_the_provider(self, snapshot: FactSnapshot) -> None:
        """It describes the questions, not the answers.

        Two providers asked the same questions carry the same value, which is
        what makes it usable for deciding whether two reports are comparable.
        """
        first = evaluate(snapshot, FixtureProvider(always_abstains()))
        second = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert first.request_set_fingerprint == second.request_set_fingerprint
        assert first.model_dump_json() != second.model_dump_json()

    def test_it_is_built_from_the_requests_in_order(self, snapshot: FactSnapshot) -> None:
        """Stated directly, so the report's value is checkable by hand."""
        requests = build_requests(snapshot)
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.request_set_fingerprint == request_set_fingerprint(requests)

    def test_a_snapshot_with_no_pages_still_has_one(self) -> None:
        """An empty run has an identity too, and it is not an error."""
        bare = FactSnapshot.from_index(index_of(settlement_line("sl-1")))

        report = evaluate(bare, FixtureProvider(always_abstains()))

        assert report.request_set_fingerprint == request_set_fingerprint(())
        assert len(report.request_set_fingerprint) == 64
