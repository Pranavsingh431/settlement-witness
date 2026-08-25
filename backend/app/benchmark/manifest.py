"""The manifest: what was generated, and what it must produce.

The manifest is the oracle. It is written by the generator from the scenario
specifications, and read by the evaluator to grade a run. Nothing in it comes
from executing the baseline.

It is deliberately kept out of the CSV documents. The system under test sees the
documents; only the harness sees the manifest.
"""

from pydantic import BaseModel, ConfigDict

from app.benchmark.specs import OracleExpectation, ScenarioSpec, TemplateId

MANIFEST_VERSION = "1.0.0"
"""Version of the manifest format itself, so a stored manifest stays readable."""


class ScenarioEntry(BaseModel):
    """One scenario's identity, expectation and expected evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    template: TemplateId
    paired_control_id: str | None
    subject_settlement_line_id: str
    expected: OracleExpectation
    expected_evidence_record_ids: tuple[str, ...]
    """Every source record the decision must cite, in sorted order.

    Derived from the structure of the scenario: the line's own row, the rows of
    every event for its payment, and the payout's row when one exists. Not from
    watching what the baseline cited.
    """


class DocumentEntry(BaseModel):
    """One generated CSV document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_name: str
    record_type: str
    source_system: str
    row_count: int
    sha256: str


class CorpusManifest(BaseModel):
    """Everything one generated corpus is and expects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: str = MANIFEST_VERSION
    generator_version: str
    domain_schema_version: str
    parser_version: str
    seed: int
    corpus_name: str
    is_synthetic: bool = True
    """Always true. These scenarios are generated, and no result over them is a
    statement about any real merchant's records."""

    scenario_count: int
    documents: tuple[DocumentEntry, ...]
    scenarios: tuple[ScenarioEntry, ...]


def expected_evidence_for(spec: ScenarioSpec, record_ids: dict[str, str]) -> tuple[str, ...]:
    """Return the source record IDs a decision for this scenario must cite.

    Args:
        spec: The scenario.
        record_ids: Provider event ID to source record ID, for every row that was
            rendered into a document.

    Returns:
        The expected citations, sorted, matching how a decision orders them.

    The set is the line, every event for its payment, and the payout when one
    exists. That follows from what the case is, not from what the baseline did.
    """
    wanted = [spec.settlement_lines[0].provider_event_id]
    wanted.extend(event.provider_event_id for event in spec.payment_events)
    wanted.extend(payout.provider_event_id for payout in spec.payouts)
    return tuple(sorted(record_ids[provider_event_id] for provider_event_id in wanted))
