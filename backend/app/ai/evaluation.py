"""Scoring link proposals against what the baseline already links.

Shadow means exactly that: this runs beside reconciliation and changes nothing
about it. No decision, fact, receipt, run or baseline result is read for writing
or written at all. Running an evaluation twice over the same snapshot leaves the
database identical, and a test holds that.

## One question per page, one score per line

A settlement line's candidate universe is split into pages, and each page is
asked as its own request. Selections are then aggregated by line, because a line
is right or wrong as a whole: a provider that answers three pages perfectly and
never answers the fourth has not linked that line correctly.

So the page rates and the line rates are named apart. `abstention_page_rate` and
`invalid_page_rate` are over pages, and say so. `exact_set_accuracy` is over
lines and requires the aggregate selection across every page of a line to equal
that line's full linked set.

## Why the metrics are separate

A single number would hide the ways of being wrong. Selecting every candidate
scores perfect recall while linking records that do not belong; abstaining on
everything scores no false links while answering nothing. Both are useless and
each looks good under one metric, so every rate is reported side by side and
none is averaged with another.

`link_recall` is measured over every true link in the corpus, not over the pages
that happened to produce a selection. A page the provider abstained on, failed
on, or answered invalidly returned none of its true links, and a recall that
skipped those pages would let a provider raise its score by declining to answer.
`answered_link_recall` is the conditional measure, reported separately and never
called recall on its own, because it answers a different question: how well the
provider did when it did answer.

## Safe abstention is not a linking metric

Some scenarios have no safe answer: the information a provider is shown does not
identify a record, even though the private oracle knows which one it is. On
those, abstaining is correct and selecting is not, and both of those facts are
invisible to link recall, which counts an abstention as a miss.

So `safe_abstention_recall` and `unsafe_selection_rate` are reported separately
and never folded into recall. An abstention on an ambiguous case is safe and
still lowers strict recall; that tradeoff is the thing to look at, not something
to average away.

## pass@1 only

One deterministic fake provider, asked once per page. There is no sampling, no
temperature and no second attempt, so pass@k would be pass@1 reported k times.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.ai.candidates import (
    LinkProposalRequest,
    Styling,
    build_requests,
    line_truth_for,
    truth_for,
)
from app.ai.proposals import ProviderIdentity
from app.ai.provider import LinkProposalProvider, ProviderFailure
from app.ai.validation import RejectedProposal, RejectionCode, parse_proposal
from app.benchmark.metrics import Rate
from app.reconciliation.snapshot import FactSnapshot

SHADOW_HARNESS_VERSION = "3.0.0"
"""Version of these definitions.

Changes when a metric's meaning changes, so two reports carrying different
versions are not compared as though they measured the same thing.

2.0.0 redefined `link_recall` over the whole corpus rather than the answered
lines. 3.0.0 made the harness page-aware: a provider is asked once per candidate
page rather than once per line, the abstention and invalid rates became
explicitly per-page, and exact-set accuracy became an aggregate over every page
of a line. A 2.0.0 report asked a different set of questions and is not
comparable with one from here."""


class ExpectedProviderAction(StrEnum):
    """What a scenario expects of a provider. Private to the evaluator.

    Never rendered to a provider and never present in a request. It exists so
    that a case with no safe answer can be scored on whether the provider
    declined, which link recall cannot express: recall counts an abstention as a
    miss whether or not declining was the right thing to do.
    """

    SELECT_EXACTLY = "SELECT_EXACTLY"
    """The visible references identify the linked records. Selecting them is
    correct and abstaining is a miss."""

    ABSTAIN = "ABSTAIN"
    """The visible references do not identify the linked records safely, even
    though the private oracle knows them. Abstaining is correct and selecting
    anything is unsafe."""


class PageOutcome(BaseModel):
    """What happened for one candidate page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_settlement_line_id: str
    page_ordinal: int
    page_truth: tuple[str, ...]
    """The true links this page offered, in record ID order."""

    selected: tuple[str, ...]
    """What the provider selected, in record ID order. Empty when it abstained
    or was rejected."""

    abstained: bool
    rejection: RejectionCode | None
    """Set when the page's proposal was refused. Refusal is not an exception and
    not a reconciliation outcome; it is an AI-proposal failure."""

    @property
    def answered(self) -> bool:
        """Return whether a usable selection was produced for this page."""
        return self.rejection is None and not self.abstained


class LineOutcome(BaseModel):
    """What happened for one settlement line, across all of its pages."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_settlement_line_id: str
    truth: tuple[str, ...]
    """Every record the baseline links to this line, across every page."""

    selected: tuple[str, ...]
    """Everything the provider selected for this line, aggregated over pages."""

    page_count: int
    answered_page_count: int
    expected_action: ExpectedProviderAction

    @property
    def true_positives(self) -> int:
        """Selected records that the baseline also links."""
        return len(set(self.selected) & set(self.truth))

    @property
    def false_links(self) -> int:
        """Selected records the baseline does not link."""
        return len(set(self.selected) - set(self.truth))

    @property
    def exact(self) -> bool:
        """Return whether the aggregate selection is exactly the linked set.

        Over every page. A line answered perfectly on three pages and never on
        the fourth is not exact, because the records on the fourth page were
        never returned.
        """
        return set(self.selected) == set(self.truth)

    @property
    def selected_anything(self) -> bool:
        """Return whether the provider selected any record for this line."""
        return bool(self.selected)


class ShadowReport(BaseModel):
    """What one provider did across one snapshot.

    Every rate carries its counts, and a rate over no cases is null rather than
    zero, so an evaluation that measured nothing cannot read as a perfect or a
    failing one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    harness_version: str
    snapshot_fingerprint: str
    provider: ProviderIdentity
    line_count: int
    page_count: int

    link_precision: Rate
    """Of the records selected, how many the baseline also links.

    The metric that catches selecting everything."""

    link_recall: Rate
    """Of every true link in the corpus, how many were selected.

    The denominator is every line asked about and every page of it, whatever
    became of each. Abstaining, failing and returning something invalid all
    return none of that page's true links, and all count against this. Null only
    when the corpus contains no true link at all."""

    answered_link_recall: Rate
    """The same ratio over the pages that produced a selection.

    How well the provider did when it answered, which is a real question and a
    different one. Reported under a name that says which pages it covers, never
    as recall alone."""

    exact_set_accuracy: Rate
    """Lines whose aggregate selection was exactly the linked set.

    Over lines, not pages, and over every page of each line."""

    abstention_page_rate: Rate
    """Pages where the provider declined to select.

    Named for pages because that is what it counts. A line with four pages that
    was declined once contributes one to this and four to the denominator."""

    invalid_page_rate: Rate
    """Pages where the output was refused or the provider failed."""

    abstained_line_rate: Rate
    """Lines where the provider selected nothing at all, on any page."""

    false_link_rate: Rate
    """Of the records selected, how many the baseline does not link.

    The complement of precision, reported in its own right because a false link
    is the specific harm: a record presented as related to a settlement line
    when nothing in the records says it is."""

    safe_abstention_recall: Rate
    """Of the lines where abstaining was the safe answer, how many abstained.

    Reported apart from linking. An abstention on such a line is correct and
    still lowers strict recall, and averaging the two would hide exactly that
    tradeoff. Null when no line in the corpus expects an abstention."""

    unsafe_selection_rate: Rate
    """Of the lines where abstaining was the safe answer, how many selected.

    The harm this pair exists to measure: a link asserted from information that
    does not identify a record."""

    line_outcomes: tuple[LineOutcome, ...]
    """Every line, in settlement line ID order."""

    page_outcomes: tuple[PageOutcome, ...]
    """Every page, in line then page order, so a number traces to the questions
    that produced it."""


def _evaluate_page(
    request: LinkProposalRequest, snapshot: FactSnapshot, provider: LinkProposalProvider
) -> PageOutcome:
    """Ask a provider about one page and record what came back."""
    page_truth = tuple(sorted(truth_for(request, snapshot)))
    result = provider.propose(request)

    # A failure's identity is read from the provider object too. Taking it from
    # the failure payload would let a provider that failed report the failure
    # against somebody else's name.
    if isinstance(result, ProviderFailure):
        return PageOutcome(
            subject_settlement_line_id=request.subject_settlement_line_id,
            page_ordinal=request.page_ordinal,
            page_truth=page_truth,
            selected=(),
            abstained=False,
            rejection=RejectionCode.PROVIDER_FAILED,
        )

    validated = parse_proposal(result, request, provider.identity)
    if isinstance(validated, RejectedProposal):
        return PageOutcome(
            subject_settlement_line_id=request.subject_settlement_line_id,
            page_ordinal=request.page_ordinal,
            page_truth=page_truth,
            selected=(),
            abstained=False,
            rejection=validated.code,
        )

    return PageOutcome(
        subject_settlement_line_id=request.subject_settlement_line_id,
        page_ordinal=request.page_ordinal,
        page_truth=page_truth,
        selected=tuple(sorted(validated.selected)),
        abstained=validated.abstained,
        rejection=None,
    )


def evaluate(
    snapshot: FactSnapshot,
    provider: LinkProposalProvider,
    expected: Mapping[str, ExpectedProviderAction] | None = None,
    styling: Styling | None = None,
) -> ShadowReport:
    """Ask a provider about every page of every line and score the answers.

    Reads the snapshot and writes nothing anywhere.

    Args:
        snapshot: The facts to ask about.
        provider: The provider to ask. In this phase, always a fixture.
        expected: What each line expects of a provider, by settlement line ID.
            Private to the evaluator and never rendered to the provider. Lines
            absent from it are treated as expecting a selection.
        styling: How each record's reference is written when it is shown. Empty
            means canonical throughout, which is what every caller outside the
            shadow corpus uses.

    Returns:
        The report, with lines in settlement line ID order and pages in line
        then page order.
    """
    requests = build_requests(snapshot, styling)
    pages = tuple(_evaluate_page(request, snapshot, provider) for request in requests)
    lines = _aggregate(requests, pages, snapshot, expected or {})
    return _report(snapshot, provider.identity, lines, pages)


def _aggregate(
    requests: Sequence[LinkProposalRequest],
    pages: Sequence[PageOutcome],
    snapshot: FactSnapshot,
    expected: Mapping[str, ExpectedProviderAction],
) -> tuple[LineOutcome, ...]:
    """Fold page outcomes into one outcome per settlement line."""
    by_line: dict[str, LinkProposalRequest] = {}
    for request in requests:
        by_line.setdefault(request.subject_settlement_line_id, request)

    outcomes: list[LineOutcome] = []
    for line_id in sorted(by_line):
        of_line = [page for page in pages if page.subject_settlement_line_id == line_id]
        selected = sorted({record for page in of_line for record in page.selected})
        outcomes.append(
            LineOutcome(
                subject_settlement_line_id=line_id,
                truth=tuple(sorted(line_truth_for(by_line[line_id], snapshot))),
                selected=tuple(selected),
                page_count=len(of_line),
                answered_page_count=sum(1 for page in of_line if page.answered),
                expected_action=expected.get(line_id, ExpectedProviderAction.SELECT_EXACTLY),
            )
        )
    return tuple(outcomes)


def _report(
    snapshot: FactSnapshot,
    provider: ProviderIdentity,
    lines: Sequence[LineOutcome],
    pages: Sequence[PageOutcome],
) -> ShadowReport:
    """Assemble the rates from the per-line and per-page outcomes."""
    selected_total = sum(len(line.selected) for line in lines)
    true_positives = sum(line.true_positives for line in lines)
    false_links = sum(line.false_links for line in lines)

    # Every true link in the corpus, whatever became of the page carrying it.
    linkable = sum(len(line.truth) for line in lines)

    # The conditional measure, over the pages that produced a selection.
    answered_pages = [page for page in pages if page.answered]
    answered_linkable = sum(len(page.page_truth) for page in answered_pages)
    answered_true_positives = sum(
        len(set(page.selected) & set(page.page_truth)) for page in answered_pages
    )

    expecting_abstention = [
        line for line in lines if line.expected_action is ExpectedProviderAction.ABSTAIN
    ]

    return ShadowReport(
        harness_version=SHADOW_HARNESS_VERSION,
        snapshot_fingerprint=snapshot.digest,
        provider=provider,
        line_count=len(lines),
        page_count=len(pages),
        link_precision=Rate.of(true_positives, selected_total),
        link_recall=Rate.of(true_positives, linkable),
        answered_link_recall=Rate.of(answered_true_positives, answered_linkable),
        exact_set_accuracy=Rate.of(sum(1 for line in lines if line.exact), len(lines)),
        abstention_page_rate=Rate.of(sum(1 for page in pages if page.abstained), len(pages)),
        invalid_page_rate=Rate.of(
            sum(1 for page in pages if page.rejection is not None), len(pages)
        ),
        abstained_line_rate=Rate.of(
            sum(1 for line in lines if not line.selected_anything), len(lines)
        ),
        false_link_rate=Rate.of(false_links, selected_total),
        safe_abstention_recall=Rate.of(
            sum(1 for line in expecting_abstention if not line.selected_anything),
            len(expecting_abstention),
        ),
        unsafe_selection_rate=Rate.of(
            sum(1 for line in expecting_abstention if line.selected_anything),
            len(expecting_abstention),
        ),
        line_outcomes=tuple(lines),
        page_outcomes=tuple(pages),
    )
