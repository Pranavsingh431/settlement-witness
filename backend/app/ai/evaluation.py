"""Scoring link proposals against what the baseline already links.

Shadow means exactly that: this runs beside reconciliation and changes nothing
about it. No decision, fact, receipt, run or baseline result is read for writing
or written at all. Running an evaluation twice over the same snapshot leaves the
database identical, and a test holds that.

## Why the metrics are separate

A single number would hide the two ways of being wrong. Selecting every
candidate scores perfect recall while linking records that do not belong;
abstaining on everything scores no false links while answering nothing. Both are
useless and each looks good under one metric, so precision, recall, exact-set
accuracy, abstention rate, invalid-output rate and false-link rate are reported
side by side and never averaged.

**None of these is a reconciliation accuracy.** They measure whether a provider
picked the records the deterministic linker already picks. Reconciliation
correctness is what the verifier derives, and nothing here contributes to it.

## pass@1 only

One deterministic fake provider, asked once per line. There is no sampling, no
temperature and no second attempt, so pass@k would be pass@1 reported k times.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from app.ai.candidates import LinkProposalRequest, build_requests, truth_for
from app.ai.proposals import ProviderIdentity
from app.ai.provider import LinkProposalProvider, ProviderFailure
from app.ai.validation import (
    RejectedProposal,
    RejectionCode,
    parse_proposal,
)
from app.benchmark.metrics import Rate
from app.reconciliation.snapshot import FactSnapshot

SHADOW_HARNESS_VERSION = "1.0.0"
"""Version of these definitions.

Changes when a metric's meaning changes, so two reports carrying different
versions are not compared as though they measured the same thing."""


class LineOutcome(BaseModel):
    """What happened for one settlement line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_settlement_line_id: str
    truth: tuple[str, ...]
    """The records the deterministic baseline links, in record ID order."""

    selected: tuple[str, ...]
    """What the provider selected, in record ID order. Empty when it abstained
    or was rejected."""

    abstained: bool
    rejection: RejectionCode | None
    """Set when the proposal was refused. Refusal is not an exception and not a
    reconciliation outcome; it is an AI-proposal failure."""

    @property
    def answered(self) -> bool:
        """Return whether a usable selection was produced for this line."""
        return self.rejection is None and not self.abstained

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
        """Return whether the selection is exactly the linked set."""
        return self.answered and set(self.selected) == set(self.truth)


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

    link_precision: Rate
    """Of the records selected, how many the baseline also links.

    The metric that catches selecting everything."""

    link_recall: Rate
    """Of the records the baseline links, how many were selected."""

    exact_set_accuracy: Rate
    """Lines where the selection was exactly the linked set.

    Reported beside recall because a broad selection can have perfect recall and
    be exactly right nowhere."""

    abstention_rate: Rate
    """Lines where the provider declined to select."""

    invalid_output_rate: Rate
    """Lines where the output was refused by the validator or the provider
    failed. Counted over every line asked, so it cannot be diluted."""

    false_link_rate: Rate
    """Of the records selected, how many the baseline does not link.

    The complement of precision, reported in its own right because a false link
    is the specific harm: a record presented as related to a settlement line
    when nothing in the records says it is."""

    outcomes: tuple[LineOutcome, ...]
    """Every line, in settlement line ID order, so a report is reproducible and
    a number can be traced to the lines that produced it."""


def _evaluate_one(
    request: LinkProposalRequest, snapshot: FactSnapshot, provider: LinkProposalProvider
) -> LineOutcome:
    """Ask a provider about one line and record what came back."""
    truth = tuple(sorted(truth_for(request, snapshot)))
    result = provider.propose(request)

    if isinstance(result, ProviderFailure):
        return LineOutcome(
            subject_settlement_line_id=request.subject_settlement_line_id,
            truth=truth,
            selected=(),
            abstained=False,
            rejection=RejectionCode.PROVIDER_FAILED,
        )

    validated = parse_proposal(result, request)
    if isinstance(validated, RejectedProposal):
        return LineOutcome(
            subject_settlement_line_id=request.subject_settlement_line_id,
            truth=truth,
            selected=(),
            abstained=False,
            rejection=validated.code,
        )

    return LineOutcome(
        subject_settlement_line_id=request.subject_settlement_line_id,
        truth=truth,
        selected=tuple(sorted(validated.selected)),
        abstained=validated.abstained,
        rejection=None,
    )


def evaluate(snapshot: FactSnapshot, provider: LinkProposalProvider) -> ShadowReport:
    """Ask a provider about every settlement line and score the answers.

    Reads the snapshot and writes nothing anywhere.

    Args:
        snapshot: The facts to ask about.
        provider: The provider to ask. In this phase, always a fixture.

    Returns:
        The report, with every line in settlement line ID order.
    """
    requests = build_requests(snapshot)
    outcomes = tuple(_evaluate_one(request, snapshot, provider) for request in requests)
    return _report(snapshot, provider.identity, outcomes)


def _report(
    snapshot: FactSnapshot, provider: ProviderIdentity, outcomes: Sequence[LineOutcome]
) -> ShadowReport:
    """Assemble the rates from the per-line outcomes."""
    selected_total = sum(len(outcome.selected) for outcome in outcomes)
    true_positives = sum(outcome.true_positives for outcome in outcomes)
    false_links = sum(outcome.false_links for outcome in outcomes)

    # Recall is measured over the lines that produced a selection. A line the
    # provider abstained on or got refused on has no selection to have missed a
    # record from, and counting its truth in the denominator would make
    # abstaining look like missing.
    answered = [outcome for outcome in outcomes if outcome.answered]
    linkable = sum(len(outcome.truth) for outcome in answered)

    return ShadowReport(
        harness_version=SHADOW_HARNESS_VERSION,
        snapshot_fingerprint=snapshot.digest,
        provider=provider,
        line_count=len(outcomes),
        link_precision=Rate.of(true_positives, selected_total),
        link_recall=Rate.of(true_positives, linkable),
        exact_set_accuracy=Rate.of(sum(1 for outcome in outcomes if outcome.exact), len(outcomes)),
        abstention_rate=Rate.of(sum(1 for outcome in outcomes if outcome.abstained), len(outcomes)),
        invalid_output_rate=Rate.of(
            sum(1 for outcome in outcomes if outcome.rejection is not None), len(outcomes)
        ),
        false_link_rate=Rate.of(false_links, selected_total),
        outcomes=tuple(outcomes),
    )
