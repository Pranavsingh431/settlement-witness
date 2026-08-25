"""Command line entry point for the scenario generator and the evaluator.

Two commands:

    uv run python -m app.benchmark_cli generate --config ../benchmark/public-corpus.json \\
        --output ../data/generated/benchmark/public
    uv run python -m app.benchmark_cli evaluate --config ../benchmark/public-corpus.json

Both take a configuration file carrying the seed. Nothing is generated without
one, because a corpus whose seed nobody recorded cannot be reproduced and
therefore cannot be evidence of anything.

`evaluate` regenerates the corpus from the configuration rather than reading it
from disk, so a report always describes a corpus that the recorded seed actually
produces. Pointing it at an externally supplied private configuration is the
same command with a different path.
"""

import argparse
import json
import sys
from pathlib import Path

from app.benchmark.evaluator import render_report, run_corpus
from app.benchmark.generator import CorpusConfig, generate, write_corpus


def load_config(path: Path) -> CorpusConfig:
    """Return the corpus configuration at ``path``."""
    return CorpusConfig.model_validate_json(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for this command."""
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Generate synthetic reconciliation scenarios and evaluate the baseline.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    generator = subcommands.add_parser("generate", help="Write a corpus and its manifest")
    generator.add_argument("--config", type=Path, required=True, help="Corpus configuration")
    generator.add_argument("--output", type=Path, required=True, help="Directory to write into")

    evaluator = subcommands.add_parser("evaluate", help="Evaluate the baseline on a corpus")
    evaluator.add_argument("--config", type=Path, required=True, help="Corpus configuration")
    evaluator.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the report here as well as printing it",
    )
    evaluator.add_argument(
        "--summary-only",
        action="store_true",
        help="Print the metrics without the per scenario failures",
    )
    return parser


def _generate(args: argparse.Namespace) -> int:
    """Write a corpus to disk."""
    corpus = generate(load_config(args.config))
    written = write_corpus(corpus, args.output)

    print(f"corpus     : {corpus.manifest.corpus_name}")
    print(f"seed       : {corpus.manifest.seed}")
    print(f"scenarios  : {corpus.manifest.scenario_count}")
    print(f"synthetic  : {corpus.manifest.is_synthetic}")
    for path in written:
        print(f"wrote      : {path}")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    """Evaluate the baseline against a corpus and print the report."""
    corpus = generate(load_config(args.config))
    report = run_corpus(corpus)

    rendered = render_report(report)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")

    if args.summary_only:
        payload = report.model_dump(mode="json")
        payload.pop("failures", None)
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(rendered, end="")

    return 0 if report.pass_at_1.value == 1.0 else 1


def run(argv: list[str] | None = None) -> int:
    """Run one subcommand and return the process exit status."""
    args = build_parser().parse_args(argv)

    if not args.config.is_file():
        print(f"error: no such configuration: {args.config}", file=sys.stderr)
        return 1

    if args.command == "generate":
        return _generate(args)
    return _evaluate(args)


def main() -> None:
    """Run the command and exit with its status."""
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
