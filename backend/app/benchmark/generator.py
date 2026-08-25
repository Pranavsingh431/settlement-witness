"""The seeded scenario generator.

Given a seed and a count per template, produces a corpus of CSV documents and a
manifest. The same seed and configuration always produce byte identical output.

Randomness decides amounts only. Which shapes exist, how many of each, and what
each must produce are all fixed by the configuration and the templates, so a
different seed varies the data without varying the coverage.
"""

import csv
import io
import random
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.benchmark.manifest import (
    CorpusManifest,
    DocumentEntry,
    ScenarioEntry,
    expected_evidence_for,
)
from app.benchmark.specs import ScenarioSpec, TemplateId
from app.benchmark.templates import ANOMALY_BUILDERS, ScenarioBuilder
from app.domain.facts import SourceRecordType, SourceSystem
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.ingestion.parsing import compute_document_hash, derive_source_record_id
from app.ingestion.schemas import PARSER_VERSION, expected_headers

GENERATOR_VERSION = "1.0.0"
"""Version of the generation rules.

Recorded in every manifest. It changes when a template, an amount rule, or the
identifier scheme changes, because any of those makes two corpora with the same
seed different.
"""

SOURCE_SYSTEM = SourceSystem.PSP_API
"""Every generated document is declared as coming from the provider API."""

#: Gross amounts are drawn from this range, in minor units, and rounded so that
#: the two percent fee and eighteen percent tax stay whole numbers. Without that
#: the control cases would fail INV-002 through rounding rather than through any
#: modelled fault, and the corpus would be measuring the wrong thing.
MIN_GROSS_MINOR = 50_000
MAX_GROSS_MINOR = 5_000_000
GROSS_STEP = 5_000


class CorpusConfig(BaseModel):
    """What corpus to generate.

    Committed for the public corpus, supplied externally for a private one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_name: str
    seed: int
    controls_per_anomaly: int = Field(default=1, ge=1)
    """How many control and anomaly pairs to build for each anomalous template."""

    extra_controls: int = Field(default=0, ge=0)
    """Standalone controls, beyond the one paired with each anomaly."""

    @property
    def scenario_count(self) -> int:
        """Return how many scenarios this configuration produces."""
        paired = len(ANOMALY_BUILDERS) * self.controls_per_anomaly
        return paired * 2 + self.extra_controls


def _draw_gross(rng: random.Random) -> int:
    """Return a gross amount whose fee and tax are whole minor units."""
    steps = (MAX_GROSS_MINOR - MIN_GROSS_MINOR) // GROSS_STEP
    return MIN_GROSS_MINOR + rng.randint(0, steps) * GROSS_STEP


def build_scenarios(config: CorpusConfig) -> tuple[ScenarioSpec, ...]:
    """Return every scenario for a configuration, in a fixed order.

    Each anomaly is generated together with a control built from the same
    amounts, so the pair differs only in the one thing the anomaly changes.
    """
    rng = random.Random(config.seed)  # noqa: S311
    scenarios: list[ScenarioSpec] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"SW-{counter:05d}"

    for template in sorted(ANOMALY_BUILDERS, key=lambda item: item.value):
        for _ in range(config.controls_per_anomaly):
            gross = _draw_gross(rng)
            control_id = next_id()
            anomaly_id = next_id()

            control = ScenarioBuilder(control_id, gross).resolved_direct()
            anomaly = ANOMALY_BUILDERS[template](ScenarioBuilder(anomaly_id, gross), control_id)
            scenarios.append(control)
            scenarios.append(anomaly)

    for _ in range(config.extra_controls):
        scenarios.append(ScenarioBuilder(next_id(), _draw_gross(rng)).resolved_direct())

    return tuple(scenarios)


def _render(rows: list[dict[str, object]], record_type: SourceRecordType) -> str:
    """Return rows as a CSV document matching the strict schema exactly."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    header = expected_headers(record_type)
    writer.writerow(header)
    for row in rows:
        writer.writerow([row[column] for column in header])
    return buffer.getvalue()


def _document_rows(
    scenarios: tuple[ScenarioSpec, ...],
) -> dict[SourceRecordType, list[dict[str, object]]]:
    """Return every row, grouped by record type, in scenario order."""
    rows: dict[SourceRecordType, list[dict[str, object]]] = {
        SourceRecordType.PAYMENT_EVENT: [],
        SourceRecordType.SETTLEMENT_LINE: [],
        SourceRecordType.PAYOUT: [],
    }
    for scenario in scenarios:
        for event in scenario.payment_events:
            rows[SourceRecordType.PAYMENT_EVENT].append(event.model_dump(mode="json"))
        for line in scenario.settlement_lines:
            rows[SourceRecordType.SETTLEMENT_LINE].append(line.model_dump(mode="json"))
        for payout in scenario.payouts:
            rows[SourceRecordType.PAYOUT].append(payout.model_dump(mode="json"))
    return rows


FILE_NAMES: dict[SourceRecordType, str] = {
    SourceRecordType.PAYMENT_EVENT: "payment_events.csv",
    SourceRecordType.SETTLEMENT_LINE: "settlement_lines.csv",
    SourceRecordType.PAYOUT: "payouts.csv",
}


class GeneratedCorpus(BaseModel):
    """A corpus in memory, before it is written anywhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config: CorpusConfig
    manifest: CorpusManifest
    documents: dict[str, str]
    """File name to CSV text."""


def generate(config: CorpusConfig) -> GeneratedCorpus:
    """Build a complete corpus and its manifest.

    Documents are rendered first, because a source record ID depends on the hash
    of the document it came from. The expected evidence for each scenario is then
    derived from the scenario's own structure, using those record IDs.
    """
    scenarios = build_scenarios(config)
    grouped = _document_rows(scenarios)

    documents: dict[str, str] = {}
    entries: list[DocumentEntry] = []
    record_ids: dict[str, str] = {}

    for record_type in (
        SourceRecordType.PAYMENT_EVENT,
        SourceRecordType.SETTLEMENT_LINE,
        SourceRecordType.PAYOUT,
    ):
        rows = grouped[record_type]
        text = _render(rows, record_type)
        digest = compute_document_hash(text.encode("utf-8"))
        file_name = FILE_NAMES[record_type]

        documents[file_name] = text
        entries.append(
            DocumentEntry(
                file_name=file_name,
                record_type=record_type.value,
                source_system=SOURCE_SYSTEM.value,
                row_count=len(rows),
                sha256=digest,
            )
        )
        for offset, row in enumerate(rows, start=2):
            record_ids[str(row["provider_event_id"])] = derive_source_record_id(
                digest, SOURCE_SYSTEM, record_type, offset
            )

    manifest = CorpusManifest(
        generator_version=GENERATOR_VERSION,
        domain_schema_version=DOMAIN_SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        seed=config.seed,
        corpus_name=config.corpus_name,
        scenario_count=len(scenarios),
        documents=tuple(entries),
        scenarios=tuple(
            ScenarioEntry(
                scenario_id=scenario.scenario_id,
                template=scenario.template,
                paired_control_id=scenario.paired_control_id,
                subject_settlement_line_id=scenario.subject_settlement_line_id,
                expected=scenario.expected,
                expected_evidence_record_ids=expected_evidence_for(scenario, record_ids),
            )
            for scenario in scenarios
        ),
    )

    return GeneratedCorpus(config=config, manifest=manifest, documents=documents)


def write_corpus(corpus: GeneratedCorpus, destination: Path) -> list[Path]:
    """Write a corpus to disk and return the paths written, sorted.

    Args:
        corpus: The corpus to write.
        destination: Directory to write into. Created if absent.

    Returns:
        Every path written, including the manifest.
    """
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for file_name, text in sorted(corpus.documents.items()):
        path = destination / file_name
        path.write_text(text, encoding="utf-8")
        written.append(path)

    manifest_path = destination / "manifest.json"
    manifest_path.write_text(render_manifest(corpus.manifest), encoding="utf-8")
    written.append(manifest_path)
    return written


def render_manifest(manifest: CorpusManifest) -> str:
    """Return the manifest as deterministic JSON."""
    import json

    return (
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


TEMPLATE_ORDER: tuple[TemplateId, ...] = tuple(sorted(TemplateId, key=lambda item: item.value))
"""Templates in a fixed order, for reporting."""
