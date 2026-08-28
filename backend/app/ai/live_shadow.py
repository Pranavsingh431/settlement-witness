"""Running the shadow corpus against a hosted model, from the command line.

The only way a hosted model is reached in this project. There is no API route,
no frontend page, and no other caller.

**It evaluates `build_corpus()` and nothing else.** There is no database
argument, no file argument, no snapshot argument and no way to point it at
imported documents. That is not a default a flag can change; the corpus is
built inside `run` and nothing else is reachable from here.

**It requires `--allow-network`.** Without it the command stops before the
environment is read, so a run started by accident cannot even look at a
credential, let alone send one.

Usage::

    uv run python -m app.ai.live_shadow --allow-network --output results/live.json

**The provider is authorised for the corpus and nothing else.** The allow-list
handed to the adapter is derived here, from the same `build_corpus()` and the
same canonical styling the run is scored against. So a page from anywhere else
is refused by the adapter itself, not merely absent from this command's options.

The receipt it writes carries the metrics, the configuration that produced them
and two separate sets of counts: what the shared report rejected, and what the
adapter itself typed each failure as. It carries no prompt, no response, no
header, no model prose and no key.
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx2
from pydantic import BaseModel, ConfigDict

from app.ai.candidates import build_requests
from app.ai.corpus import CORPUS_VERSION, build_corpus
from app.ai.evaluation import SHADOW_HARNESS_VERSION, ShadowReport, evaluate
from app.ai.hosted import HostedLinkProposalProvider, HostedProviderConfig, MissingConfiguration
from app.reconciliation.snapshot import FactSnapshot

LIVE_RECEIPT_VERSION: Final = "2.0.0"
"""The schema of the local run receipt.

Versioned because a receipt is an artifact somebody keeps, and a reader needs to
know whether the file in front of them has the fields they expect.

1.0.0 was the unversioned shape Phase 10 wrote: one `failure_counts` field
holding the shared report's rejection codes. 2.0.0 renames that to
`report_rejection_counts`, adds `typed_failure_counts` from the adapter, and
adds this field. A 1.0.0 receipt cannot be read as a 2.0.0 one and is not
converted: there is no reason to migrate a local artifact of a run that already
happened."""


class LiveShadowRunReceipt(BaseModel):
    """What one hosted run against the shadow corpus produced.

    Written locally and never committed. It records enough to say which model
    produced which numbers under which settings, and deliberately nothing about
    what was said: no prompt, no response, no header, no error body, no
    reasoning and no key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_version: str
    """The schema of this file. See `LIVE_RECEIPT_VERSION`."""

    harness_version: str
    corpus_version: str
    provider_name: str
    model_id: str
    configuration: dict[str, object]
    """The non-secret settings. Built by `HostedProviderConfig.provenance`,
    which has no branch that could include the key."""

    requests_made: int
    report_rejection_counts: dict[str, int]
    """How many pages the shared report rejected, by rejection code.

    Generic on purpose. `PROVIDER_FAILED` is the right word for a report that
    also scores fixtures, where a timeout and a refusal really are the same
    fact. It is the wrong word for diagnosing a hosted run, which is what the
    next field is for."""

    typed_failure_counts: dict[str, int]
    """How many pages ended in each `FailureKind`, as the adapter typed it.

    Read from the adapter rather than derived from the report, because the
    report never held it. A rate limit, an unreachable host, a timeout and an
    unauthorised page are four different problems and four different fixes, and
    all four are one word in the field above.

    Counts only. No status text, no error body, no header, no provider prose."""

    report: ShadowReport
    ran_at: str
    """When this local run happened, in UTC.

    The one value here that is not reproducible, and the only one that should
    be: a receipt describes an event, and two runs of the same corpus against
    the same model are two events."""


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for this command.

    Deliberately small. There is no option that changes what is evaluated,
    because a flag that pointed this at a real snapshot is the one mistake this
    command must not make possible.
    """
    parser = argparse.ArgumentParser(
        prog="live-shadow",
        description=(
            "Evaluate the generated shadow corpus against a hosted model. "
            "Never touches the application database or any imported document."
        ),
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Required. Without it the command stops before reading any credential.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Where to write the run receipt. Nothing is written without it.",
    )
    return parser


def rejection_counts(report: ShadowReport) -> dict[str, int]:
    """Return how many pages the report rejected, by rejection code."""
    counts: dict[str, int] = {}
    for page in report.page_outcomes:
        if page.rejection is not None:
            counts[page.rejection.value] = counts.get(page.rejection.value, 0) + 1
    return dict(sorted(counts.items()))


def run(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> int:
    """Evaluate the shadow corpus against a hosted model.

    Args:
        argv: Command line arguments.
        environment: Where the configuration is read from. Passed in so a test
            never depends on ambient state.
        transport: Serves the requests in process when given, so the tests that
            cover this path reach no network. None in a live run.

    Returns:
        The process exit status. Zero when the run completed, whatever the model
        scored: a poor score is a result, not a failure of the command.
    """
    args = build_parser().parse_args(argv)

    if not args.allow_network:
        print(
            "error: this command calls a hosted model over the network. "
            "Re-run with --allow-network to allow that.",
            file=sys.stderr,
        )
        return 2

    try:
        config = HostedProviderConfig.from_environment(
            environment if environment is not None else os.environ
        )
    except MissingConfiguration as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    corpus = build_corpus()
    snapshot = FactSnapshot.from_index(corpus.index)
    # The allow-list the adapter is held to. Built from the same corpus, the
    # same snapshot and the same styling the run is scored against, so it holds
    # exactly the pages `evaluate` is about to ask about and no others.
    authorised = frozenset(
        page.request_fingerprint for page in build_requests(snapshot, corpus.styling)
    )

    with HostedLinkProposalProvider(
        config, authorised_requests=authorised, transport=transport
    ) as provider:
        report = evaluate(snapshot, provider, corpus.expected_actions, corpus.styling)
        made = provider.requests_made
        typed = provider.typed_failure_counts

    receipt = LiveShadowRunReceipt(
        receipt_version=LIVE_RECEIPT_VERSION,
        harness_version=SHADOW_HARNESS_VERSION,
        corpus_version=CORPUS_VERSION,
        provider_name=provider.identity.name,
        model_id=config.model,
        configuration=config.provenance(),
        requests_made=made,
        report_rejection_counts=rejection_counts(report),
        typed_failure_counts=typed,
        report=report,
        ran_at=datetime.now(UTC).isoformat(),
    )

    print(describe(receipt))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
        print(f"receipt written to {args.output}")
    return 0


def describe(receipt: LiveShadowRunReceipt) -> str:
    """Return a one screen summary of a live run."""
    report = receipt.report

    def rate(name: str) -> str:
        value = getattr(report, name)
        shown = "not measurable" if value.value is None else f"{value.value:.3f}"
        return f"{shown} ({value.numerator}/{value.denominator})"

    lines = [
        f"model            : {receipt.model_id}",
        f"harness / corpus : {receipt.harness_version} / {receipt.corpus_version}",
        f"request set      : {report.request_set_fingerprint[:16]}…",
        f"requests made    : {receipt.requests_made}",
        f"lines / pages    : {report.line_count} / {report.page_count}",
        f"link precision   : {rate('link_precision')}",
        f"link recall      : {rate('link_recall')}",
        f"answered recall  : {rate('answered_link_recall')}",
        f"exact set        : {rate('exact_set_accuracy')}",
        f"false link       : {rate('false_link_rate')}",
        f"safe abstention  : {rate('safe_abstention_recall')}",
        f"unsafe selection : {rate('unsafe_selection_rate')}",
        f"unusable abstain : {rate('unusable_expected_abstention_rate')}",
        f"abstention pages : {rate('abstention_page_rate')}",
        f"invalid pages    : {rate('invalid_page_rate')}",
    ]
    if receipt.report_rejection_counts:
        lines.append(f"report rejections: {receipt.report_rejection_counts}")
    if receipt.typed_failure_counts:
        lines.append(f"typed failures   : {receipt.typed_failure_counts}")
    lines.append(
        "This is one run over a generated shadow corpus. It is not "
        "reconciliation accuracy and not production performance."
    )
    return "\n".join(lines)


def main() -> None:
    """Run the command and exit with its status."""
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
