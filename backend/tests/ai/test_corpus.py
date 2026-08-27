"""Tests for the generated shadow corpus and its private oracle.

Three things must stay apart: the canonical facts, what a provider is shown, and
what a provider ought to do. Most of these check that a boundary between two of
them holds.

The leak scan is the one worth reading first. A corpus whose provider-visible
fields carried the scenario name or the answer would score a provider on
recognising a label, and the score would look like selection.
"""

import pytest

from app.ai.candidates import build_requests, line_truth_for, truth_for
from app.ai.corpus import (
    CORPUS_VERSION,
    ScenarioFamily,
    build_corpus,
    opaque,
    rendered_input,
)
from app.ai.evaluation import ExpectedProviderAction, evaluate
from app.ai.presentation import ReferenceStyle
from app.ai.provider import (
    FixtureProvider,
    always_abstains,
    matching_visible_references,
    selecting,
    selects_everything,
)
from app.reconciliation.snapshot import FactSnapshot


@pytest.fixture(scope="module")
def corpus() -> object:
    """Return the generated corpus once, since it is deterministic."""
    return build_corpus()


@pytest.fixture(scope="module")
def snapshot(corpus: object) -> FactSnapshot:
    """Return the snapshot over the corpus facts."""
    return FactSnapshot.from_index(corpus.index)  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def shown(corpus: object, snapshot: FactSnapshot) -> str:
    """Return everything a provider can read, as one string."""
    return rendered_input(build_requests(snapshot, corpus.styling))  # type: ignore[attr-defined]


class TestTheCorpusIsDeterministic:
    """Same corpus everywhere, or reports cannot be compared."""

    def test_two_builds_are_identical(self) -> None:
        """No clock, no randomness, no environment."""
        assert build_corpus().model_dump_json() == build_corpus().model_dump_json()

    def test_it_declares_its_version(self, corpus: object) -> None:
        """So two reports over different generators are not compared."""
        assert corpus.version == CORPUS_VERSION  # type: ignore[attr-defined]

    def test_opaque_tokens_are_stable_and_distinct(self) -> None:
        """The identifier scheme every generated ID goes through."""
        assert opaque("a", 1) == opaque("a", 1)
        assert opaque("a", 1) != opaque("a", 2)
        assert opaque("a", 1) != opaque("b", 1)

    def test_the_report_is_identical_across_two_runs(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The whole way through the harness."""
        provider = FixtureProvider(matching_visible_references())
        first = evaluate(snapshot, provider, corpus.expected_actions, corpus.styling)  # type: ignore[attr-defined]
        second = evaluate(snapshot, provider, corpus.expected_actions, corpus.styling)  # type: ignore[attr-defined]

        assert first.model_dump_json() == second.model_dump_json()


class TestTheCorpusComposition:
    """What is in it, reported rather than assumed."""

    def test_every_family_is_present(self, corpus: object) -> None:
        """A missing family would silently stop being measured."""
        present = {scenario.family for scenario in corpus.scenarios}  # type: ignore[attr-defined]

        assert present == set(ScenarioFamily)

    def test_the_composition_is_reportable(self, corpus: object) -> None:
        """Every denominator in the report traces back to this."""
        composition = corpus.composition()  # type: ignore[attr-defined]

        assert sum(composition.values()) == len(corpus.scenarios)  # type: ignore[attr-defined]
        assert set(composition) == {family.value for family in ScenarioFamily}

    def test_two_families_expect_an_abstention(self, corpus: object) -> None:
        """The ones with no safe answer from what is shown."""
        abstaining = {
            scenario.family
            for scenario in corpus.scenarios  # type: ignore[attr-defined]
            if scenario.expected_action is ExpectedProviderAction.ABSTAIN
        }

        assert abstaining == {
            ScenarioFamily.AMBIGUOUS_VISIBLE_REFERENCE,
            ScenarioFamily.WITHHELD_VISIBLE_REFERENCE,
        }

    def test_the_multi_page_family_spans_pages(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """Its true links are on more than one page, which is its whole point."""
        scenario = next(
            one
            for one in corpus.scenarios  # type: ignore[attr-defined]
            if one.family is ScenarioFamily.MULTI_PAGE_TARGET
        )
        pages = [
            page
            for page in build_requests(snapshot, corpus.styling)  # type: ignore[attr-defined]
            if page.subject_settlement_line_id == scenario.settlement_line_id
        ]

        with_truth = [page for page in pages if truth_for(page, snapshot)]

        assert len(with_truth) >= 3


class TestNothingPrivateIsRendered:
    """The leak scan.

    Anything a provider can read is in `shown`. If a scenario name, an expected
    action, a template name or a canonical answer appears there, the corpus is
    measuring recognition rather than selection.
    """

    @pytest.mark.parametrize("family", list(ScenarioFamily))
    def test_no_scenario_name_appears(self, shown: str, family: ScenarioFamily) -> None:
        """Not the enum value, and not a readable form of it."""
        assert family.value not in shown
        assert family.value.lower() not in shown.lower()
        assert family.value.replace("_", " ").lower() not in shown.lower()

    @pytest.mark.parametrize("action", list(ExpectedProviderAction))
    def test_no_expected_action_appears(self, shown: str, action: ExpectedProviderAction) -> None:
        """A provider must not be told what it is expected to do."""
        assert action.value not in shown
        assert action.value.lower() not in shown.lower()

    @pytest.mark.parametrize("word", ["abstain", "select_exactly", "expected", "oracle", "truth"])
    def test_no_action_word_appears(self, shown: str, word: str) -> None:
        """Including the words a label would be written in."""
        assert word not in shown.lower()

    @pytest.mark.parametrize(
        "name", ["scenario", "family", "distractor", "near_miss", "twin", "manifest", "styling"]
    )
    def test_no_template_name_appears(self, shown: str, name: str) -> None:
        """The generator's own vocabulary stays in the generator."""
        assert name not in shown.lower()

    @pytest.mark.parametrize("style", list(ReferenceStyle))
    def test_no_style_name_appears(self, shown: str, style: ReferenceStyle) -> None:
        """How a reference was rendered is not something to render."""
        assert style.value not in shown
        assert style.value.lower() not in shown.lower()

    def test_no_scenario_declares_its_answer_in_a_visible_field(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The canonical answer is not written anywhere a provider reads.

        Record IDs are necessarily visible, because a provider selects by them.
        What must not be visible is which of them is correct, so this checks that
        the answer is never marked out: no field says so, and the manifest that
        does is never rendered.
        """
        requests = build_requests(snapshot, corpus.styling)  # type: ignore[attr-defined]

        for request in requests:
            rendered = request.model_dump_json()
            assert "linked_record_ids" not in rendered
            assert "expected_action" not in rendered
            assert "family" not in rendered

    def test_the_manifest_is_never_part_of_a_request(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """It is passed to the evaluator, not to a provider."""
        from app.ai.candidates import LinkProposalRequest

        fields = set(LinkProposalRequest.model_fields)

        assert "scenarios" not in fields
        assert "expected_action" not in fields
        assert "family" not in fields


class TestTheOracleIsCanonical:
    """Truth comes from the facts, never from what was shown."""

    def test_the_manifest_agrees_with_the_baseline(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """Two independent statements of the answer, compared.

        The generator records what it linked; the evaluator computes it from the
        facts by the same exact matching the baseline uses. If those disagreed,
        one of them would be wrong and the corpus would be scoring against it.
        """
        by_line = {
            page.subject_settlement_line_id: page
            for page in build_requests(snapshot, corpus.styling)  # type: ignore[attr-defined]
        }

        for scenario in corpus.scenarios:  # type: ignore[attr-defined]
            computed = line_truth_for(by_line[scenario.settlement_line_id], snapshot)

            assert computed == set(scenario.linked_record_ids), scenario.family

    def test_corrupting_a_shown_reference_leaves_truth_unmoved(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The isolation check.

        Rendering every reference as a near miss changes what a provider sees on
        every page. The canonical answer must not move, because it was never
        read from there.
        """
        control = build_requests(snapshot, corpus.styling)  # type: ignore[attr-defined]
        corrupted_styling = dict.fromkeys(snapshot.facts_by_record_id, ReferenceStyle.NEAR_MISS)
        corrupted = build_requests(snapshot, corrupted_styling)

        assert [page.subject_payment_id for page in corrupted] != [
            page.subject_payment_id for page in control
        ]
        for before, after in zip(control, corrupted, strict=True):
            assert line_truth_for(before, snapshot) == line_truth_for(after, snapshot)

    def test_withholding_every_reference_leaves_truth_unmoved(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The strongest version: show nothing, and the answer is the same."""
        control = build_requests(snapshot, corpus.styling)  # type: ignore[attr-defined]
        blind = build_requests(
            snapshot,
            dict.fromkeys(snapshot.facts_by_record_id, ReferenceStyle.WITHHELD),
        )

        assert all(page.subject_payment_id is None for page in blind)
        for before, after in zip(control, blind, strict=True):
            assert line_truth_for(before, snapshot) == line_truth_for(after, snapshot)


class TestTheCorpusIsNotTrivial:
    """It has to be able to tell providers apart."""

    def _report(self, snapshot: FactSnapshot, corpus: object, behaviour: object) -> object:
        return evaluate(
            snapshot,
            FixtureProvider(behaviour),  # type: ignore[arg-type]
            corpus.expected_actions,  # type: ignore[attr-defined]
            corpus.styling,  # type: ignore[attr-defined]
        )

    def test_selecting_everything_is_caught(self, corpus: object, snapshot: FactSnapshot) -> None:
        """Perfect recall, and precision that says what it cost."""
        report = self._report(snapshot, corpus, selects_everything())

        assert report.link_recall.value == 1.0  # type: ignore[attr-defined]
        assert report.link_precision.value < 0.5  # type: ignore[attr-defined]
        assert report.exact_set_accuracy.value == 0.0  # type: ignore[attr-defined]

    def test_abstaining_everywhere_is_caught(self, corpus: object, snapshot: FactSnapshot) -> None:
        """Zero recall where truth exists, and safe on the two that expect it."""
        report = self._report(snapshot, corpus, always_abstains())

        assert report.link_recall.value == 0.0  # type: ignore[attr-defined]
        assert report.safe_abstention_recall.value == 1.0  # type: ignore[attr-defined]
        assert report.unsafe_selection_rate.value == 0.0  # type: ignore[attr-defined]

    def test_a_matcher_reading_the_shown_fields_does_well_but_not_perfectly(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The corpus is answerable and is not a lookup.

        A matcher that reads the rendered references sensibly links almost
        everything and cannot link the two cases that were designed to be
        unlinkable from what is shown.
        """
        report = self._report(snapshot, corpus, matching_visible_references())

        assert report.link_precision.value == 1.0  # type: ignore[attr-defined]
        assert 0.9 < report.link_recall.value < 1.0  # type: ignore[attr-defined]
        assert report.exact_set_accuracy.value < 1.0  # type: ignore[attr-defined]

    def test_the_matcher_abstains_where_abstaining_is_safe(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The behaviour the two families exist to measure."""
        report = self._report(snapshot, corpus, matching_visible_references())

        assert report.safe_abstention_recall.value == 1.0  # type: ignore[attr-defined]
        assert report.unsafe_selection_rate.value == 0.0  # type: ignore[attr-defined]

    def test_an_oracle_perfect_provider_is_unsafe(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The tradeoff this corpus exists to make visible.

        A provider that selects the canonical answer everywhere scores perfectly
        on every linking metric, and links records on both cases where nothing
        shown identified them. That is not better; it is a different failure, and
        averaging the two sets of metrics would hide it.
        """
        report = self._report(
            snapshot, corpus, selecting(lambda request: tuple(sorted(truth_for(request, snapshot))))
        )

        assert report.link_recall.value == 1.0  # type: ignore[attr-defined]
        assert report.exact_set_accuracy.value == 1.0  # type: ignore[attr-defined]
        assert report.safe_abstention_recall.value == 0.0  # type: ignore[attr-defined]
        assert report.unsafe_selection_rate.value == 1.0  # type: ignore[attr-defined]

    def test_changing_the_provider_changes_the_score(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """The perturbation check.

        A corpus that scored every provider the same would be measuring nothing.
        Four behaviours, four distinct reports.
        """
        reports = {
            label: self._report(snapshot, corpus, behaviour).model_dump_json()  # type: ignore[attr-defined]
            for label, behaviour in (
                ("matcher", matching_visible_references()),
                ("everything", selects_everything()),
                ("abstains", always_abstains()),
                (
                    "oracle",
                    selecting(lambda request: tuple(sorted(truth_for(request, snapshot)))),
                ),
            )
        }

        assert len(set(reports.values())) == len(reports)


class TestRenderingEveryStyle:
    """Each style, and the two fallbacks that only fire on unusual input."""

    @pytest.mark.parametrize(
        ("style", "expected"),
        [
            (ReferenceStyle.CANONICAL, "pay-7f3a-0001"),
            (ReferenceStyle.DASHED, "pay-7f3a-0001"),
            (ReferenceStyle.UNDERSCORED, "pay_7f3a_0001"),
            (ReferenceStyle.UPPERCASED, "PAY-7F3A-0001"),
            (ReferenceStyle.SPACED, "pay 7f3a 0001"),
            (ReferenceStyle.NEAR_MISS, "pay-7f3a-0002"),
            (ReferenceStyle.TRUNCATED, "pay-7f3a"),
            (ReferenceStyle.WITHHELD, None),
        ],
    )
    def test_a_reference_renders_as_documented(
        self, style: ReferenceStyle, expected: str | None
    ) -> None:
        """One row per style, so a change to any of them is visible."""
        from app.ai.presentation import render_reference

        assert render_reference("pay-7f3a-0001", style) == expected

    def test_a_near_miss_of_a_reference_with_no_digits_changes_a_letter(self) -> None:
        """The fallback. A near miss has to differ, digits or not."""
        from app.ai.presentation import render_reference

        assert render_reference("payref", ReferenceStyle.NEAR_MISS) == "payrex"

    def test_a_near_miss_of_a_reference_ending_in_x_uses_another_letter(self) -> None:
        """So the fallback cannot return the value unchanged."""
        from app.ai.presentation import render_reference

        assert render_reference("prefix", ReferenceStyle.NEAR_MISS) == "prefiy"

    def test_truncating_a_single_segment_leaves_it_whole(self) -> None:
        """There is no tail to drop, so nothing is dropped."""
        from app.ai.presentation import render_reference

        assert render_reference("payref", ReferenceStyle.TRUNCATED) == "payref"

    @pytest.mark.parametrize(
        ("first", "second", "same"),
        [
            ("pay-1", "PAY_1", True),
            ("pay 1", "pay-1", True),
            ("pay-1", "pay-2", False),
            (None, "pay-1", False),
            ("pay-1", None, False),
            (None, None, False),
        ],
    )
    def test_equivalence_ignores_format_and_nothing_else(
        self, first: str | None, second: str | None, same: bool
    ) -> None:
        """Case and separators are formatting. A different digit is not, and an
        absent reference matches nothing at all."""
        from app.ai.presentation import equivalent

        assert equivalent(first, second) is same


class TestTheMatcherOnUnusualPages:
    """The matcher's two guards, on pages that trigger them."""

    def test_it_selects_nothing_when_the_line_reference_is_withheld(
        self, snapshot: FactSnapshot
    ) -> None:
        """Nothing shown to match against, so nothing is matched."""
        from app.ai.candidates import build_requests as pages_of

        blind = dict.fromkeys(snapshot.facts_by_record_id, ReferenceStyle.WITHHELD)
        report = evaluate(snapshot, FixtureProvider(matching_visible_references()), None, blind)

        assert all(page.subject_payment_id is None for page in pages_of(snapshot, blind))
        assert report.link_recall.value == 0.0
        assert report.abstention_page_rate.value == 1.0

    def test_it_copes_with_a_page_where_no_candidate_shows_a_payment(
        self, corpus: object, snapshot: FactSnapshot
    ) -> None:
        """Payout-only pages exist, and the coarseness guard must not divide by
        an empty set of widths."""
        payouts_hidden = {
            record_id: ReferenceStyle.WITHHELD
            for record_id, fact in snapshot.facts_by_record_id.items()
            if fact.source_record_type.value == "PAYMENT_EVENT"
        }

        report = evaluate(
            snapshot,
            FixtureProvider(matching_visible_references()),
            corpus.expected_actions,  # type: ignore[attr-defined]
            payouts_hidden,
        )

        assert report.page_count > 0
