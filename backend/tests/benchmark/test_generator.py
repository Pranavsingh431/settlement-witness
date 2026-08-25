"""Tests for the seeded generator and its paired controls."""

import csv
import io
import json
from pathlib import Path

import pytest

from app.benchmark.generator import (
    GENERATOR_VERSION,
    CorpusConfig,
    build_scenarios,
    generate,
    render_manifest,
    write_corpus,
)
from app.benchmark.specs import (
    ANOMALY_TEMPLATES,
    SYNTHETIC_MERCHANT_PREFIX,
    ScenarioSpec,
    TemplateId,
)
from app.domain.decisions import DecisionStatus
from app.ingestion.schemas import expected_headers
from app.ingestion.service import ImportOutcome, ImportService
from app.storage.database import create_schema, session_factory
from tests.benchmark.conftest import small_config


class TestDeterminism:
    """The same seed and configuration always produce the same corpus."""

    def test_the_same_seed_produces_identical_documents(self) -> None:
        """Byte for byte, so a corpus can be reproduced from its manifest."""
        first = generate(small_config(seed=99))
        second = generate(small_config(seed=99))

        assert first.documents == second.documents

    def test_the_same_seed_produces_an_identical_manifest(self) -> None:
        """Including every expected evidence record ID."""
        first = generate(small_config(seed=99))
        second = generate(small_config(seed=99))

        assert render_manifest(first.manifest) == render_manifest(second.manifest)

    def test_a_different_seed_changes_the_corpus(self) -> None:
        """Otherwise the seed would be decoration."""
        first = generate(small_config(seed=1))
        second = generate(small_config(seed=2))

        assert first.documents != second.documents

    def test_a_different_seed_preserves_structural_validity(self) -> None:
        """Different amounts, same shapes and the same coverage."""
        first = generate(small_config(seed=1))
        second = generate(small_config(seed=2))

        assert first.manifest.scenario_count == second.manifest.scenario_count
        assert [entry.template for entry in first.manifest.scenarios] == [
            entry.template for entry in second.manifest.scenarios
        ]

    def test_the_seed_is_recorded_in_the_manifest(self) -> None:
        """A corpus whose seed nobody recorded cannot be reproduced."""
        assert generate(small_config(seed=777)).manifest.seed == 777

    def test_the_generator_version_is_recorded(self) -> None:
        """Two corpora with one seed differ if the rules changed between them."""
        assert generate(small_config()).manifest.generator_version == GENERATOR_VERSION

    def test_writing_is_stable(self, tmp_path: Path) -> None:
        """Writing the same corpus twice changes nothing on disk."""
        corpus = generate(small_config())
        write_corpus(corpus, tmp_path)
        before = {path.name: path.read_bytes() for path in sorted(tmp_path.iterdir())}
        write_corpus(corpus, tmp_path)
        after = {path.name: path.read_bytes() for path in sorted(tmp_path.iterdir())}

        assert before == after


class TestCoverage:
    """Every required template is generated."""

    def test_every_template_appears(self) -> None:
        """Ten shapes, one control and nine anomalies."""
        corpus = generate(small_config())
        seen = {entry.template for entry in corpus.manifest.scenarios}

        assert seen == set(TemplateId)

    def test_every_required_template_is_named(self) -> None:
        """The list the phase requires, held against the enum."""
        assert {template.value for template in TemplateId} == {
            "RESOLVED_DIRECT",
            "NET_FORMULA_MISMATCH",
            "CAPTURE_GROSS_MISMATCH",
            "PAYOUT_TOTAL_MISMATCH",
            "MISSING_PAYMENT",
            "MISSING_PAYOUT",
            "CURRENCY_MISMATCH",
            "PARTIAL_REFUND",
            "OUT_OF_ORDER_RETURN",
            "MULTIPLE_CAPTURES",
        }

    def test_the_public_corpus_has_at_least_fifty_scenarios(
        self, public_config: CorpusConfig
    ) -> None:
        """The committed configuration, not a number written in a document."""
        assert public_config.scenario_count >= 50
        assert generate(public_config).manifest.scenario_count >= 50

    def test_the_public_corpus_exercises_every_template(self, public_config: CorpusConfig) -> None:
        """Coverage, not just volume."""
        corpus = generate(public_config)
        seen = {entry.template for entry in corpus.manifest.scenarios}

        assert seen == set(TemplateId)


class TestPairedControls:
    """Every anomaly is matched to a control differing in one thing."""

    @staticmethod
    def _by_id(scenarios: tuple[ScenarioSpec, ...]) -> dict[str, ScenarioSpec]:
        """Return the scenarios keyed by identity."""
        return {scenario.scenario_id: scenario for scenario in scenarios}

    def test_every_anomaly_names_a_control(self) -> None:
        """No anomaly is left unpaired."""
        scenarios = build_scenarios(small_config())

        for scenario in scenarios:
            if scenario.is_control:
                assert scenario.paired_control_id is None
            else:
                assert scenario.paired_control_id is not None

    def test_every_named_control_exists_and_is_a_control(self) -> None:
        """A pointer to a missing or anomalous scenario would be worthless."""
        scenarios = build_scenarios(small_config())
        by_id = self._by_id(scenarios)

        for scenario in scenarios:
            if scenario.paired_control_id is None:
                continue
            control = by_id[scenario.paired_control_id]
            assert control.is_control
            assert control.expected.status is DecisionStatus.RESOLVED

    #: Which record collection each anomaly is allowed to differ from its control
    #: in. One entry per anomaly, declared here rather than read from the code
    #: under test, so this is a check and not a restatement.
    #:
    #: NET_FORMULA_MISMATCH also moves the payout total, because a payout total
    #: is derived from the nets of its lines. That is a consequence of the one
    #: edit, not a second edit, so it is named here and the field level check
    #: below confirms the payout changed only in the derived field.
    EXPECTED_DIFF_SCOPE: dict[TemplateId, set[str]] = {  # noqa: RUF012
        TemplateId.NET_FORMULA_MISMATCH: {"settlement_lines", "payouts"},
        TemplateId.CAPTURE_GROSS_MISMATCH: {"payment_events"},
        TemplateId.PAYOUT_TOTAL_MISMATCH: {"payouts"},
        TemplateId.MISSING_PAYMENT: {"payment_events"},
        TemplateId.MISSING_PAYOUT: {"payouts"},
        TemplateId.CURRENCY_MISMATCH: {"payment_events"},
        TemplateId.PARTIAL_REFUND: {"payment_events"},
        TemplateId.OUT_OF_ORDER_RETURN: {"payment_events"},
        TemplateId.MULTIPLE_CAPTURES: {"payment_events"},
    }

    @pytest.mark.parametrize("template", ANOMALY_TEMPLATES)
    def test_a_pair_differs_only_where_it_should(self, template: TemplateId) -> None:
        """Inspected on the structured records, not on rendered text.

        A pair that differed in three things and resolved differently would not
        have isolated anything. This compares the specifications field by field
        and requires the difference to sit exactly where the template says.
        """
        scenarios = build_scenarios(small_config())
        by_id = self._by_id(scenarios)
        anomaly = next(s for s in scenarios if s.template is template)
        control = by_id[anomaly.paired_control_id or ""]

        differing = {
            name
            for name in ("payment_events", "settlement_lines", "payouts")
            if _shape_of(getattr(anomaly, name)) != _shape_of(getattr(control, name))
        }
        assert differing == self.EXPECTED_DIFF_SCOPE[template]

    def test_a_derived_payout_change_touches_only_the_total(self) -> None:
        """The one template whose consequence reaches a second record.

        Breaking a line's net moves the payout total, because the total is the
        sum of the line nets. Every other field of the payout is untouched, so
        the pair still isolates a single edit.
        """
        scenarios = build_scenarios(small_config())
        by_id = self._by_id(scenarios)
        anomaly = next(s for s in scenarios if s.template is TemplateId.NET_FORMULA_MISMATCH)
        control = by_id[anomaly.paired_control_id or ""]

        anomaly_payout = _shape_of(anomaly.payouts)[0]
        control_payout = _shape_of(control.payouts)[0]
        changed = {key for key in control_payout if anomaly_payout[key] != control_payout[key]}
        assert changed == {"net_minor"}

    def test_the_payout_total_is_the_sum_of_its_lines(self) -> None:
        """True for every scenario except the one that breaks it deliberately."""
        for scenario in build_scenarios(small_config()):
            if scenario.template is TemplateId.PAYOUT_TOTAL_MISMATCH or not scenario.payouts:
                continue
            expected = sum(line.net_minor for line in scenario.settlement_lines)
            assert scenario.payouts[0].net_minor == expected

    def test_a_pair_shares_its_gross_amount(self) -> None:
        """The control is built from the same draw, so amounts are not the variable."""
        scenarios = build_scenarios(small_config())
        by_id = self._by_id(scenarios)

        for anomaly in scenarios:
            if anomaly.paired_control_id is None:
                continue
            if anomaly.template is TemplateId.CAPTURE_GROSS_MISMATCH:
                continue  # The gross is the intended difference here.
            control = by_id[anomaly.paired_control_id]
            assert (
                anomaly.settlement_lines[0].gross_minor == control.settlement_lines[0].gross_minor
            )

    def test_controls_and_anomalies_are_balanced(self) -> None:
        """One control per anomaly, plus any standalone extras."""
        config = CorpusConfig(
            corpus_name="balance", seed=5, controls_per_anomaly=2, extra_controls=3
        )
        scenarios = build_scenarios(config)

        controls = [s for s in scenarios if s.is_control]
        anomalies = [s for s in scenarios if not s.is_control]
        assert len(anomalies) == len(ANOMALY_TEMPLATES) * 2
        assert len(controls) == len(anomalies) + 3


def _shape_of(records: tuple[object, ...]) -> tuple[dict[str, object], ...]:
    """Return records with their scenario-specific identifiers removed.

    Two paired scenarios necessarily carry different identifiers, since they are
    different scenarios. Comparing them raw would make every pair differ
    everywhere. Stripping the identity leaves the financial shape, which is what
    a paired control is supposed to hold constant.
    """
    identity_fields = {
        "provider_event_id",
        "event_id",
        "payment_id",
        "merchant_id",
        "settlement_line_id",
        "payout_id",
        "utr",
    }
    return tuple(
        {
            key: value
            for key, value in record.model_dump(mode="json").items()  # type: ignore[attr-defined]
            if key not in identity_fields
        }
        for record in records
    )


class TestSyntheticMarking:
    """Generated data says what it is."""

    def test_every_merchant_is_marked_synthetic(self) -> None:
        """Visible in the documents themselves, not only in the manifest."""
        corpus = generate(small_config())
        rows = list(csv.DictReader(io.StringIO(corpus.documents["payment_events.csv"])))

        assert rows
        assert all(row["merchant_id"].startswith(SYNTHETIC_MERCHANT_PREFIX) for row in rows)

    def test_the_manifest_declares_it_is_synthetic(self) -> None:
        """So a report over it cannot be mistaken for a production measurement."""
        assert generate(small_config()).manifest.is_synthetic


class TestNoAnswerLabels:
    """The system under test sees documents, never the oracle."""

    FORBIDDEN = (
        "RESOLVED",
        "EXCEPTION",
        "INSUFFICIENT_EVIDENCE",
        "MISMATCH",
        "MISSING_PAYMENT",
        "MISSING_PAYOUT",
        "OUT_OF_ORDER",
        "UNSUPPORTED_STATE",
        "PARTIAL_REFUND",
        "expected",
        "oracle",
        "template",
        "paired_control",
    )

    def test_no_document_contains_an_expected_outcome(self) -> None:
        """Checked cell by cell, across every generated document."""
        corpus = generate(small_config())

        for name, text in corpus.documents.items():
            for row in csv.reader(io.StringIO(text)):
                for cell in row:
                    upper = cell.upper()
                    for label in self.FORBIDDEN:
                        assert label.upper() not in upper, f"{name} leaks {label!r}: {cell!r}"

    def test_scenario_ids_are_opaque(self) -> None:
        """They reach the documents, so they must not name the template."""
        corpus = generate(small_config())

        for entry in corpus.manifest.scenarios:
            assert entry.template.value not in entry.scenario_id
            assert entry.scenario_id.startswith("SW-")

    def test_the_manifest_is_not_one_of_the_documents(self) -> None:
        """It holds the answers, so it is never imported."""
        corpus = generate(small_config())

        assert "manifest.json" not in corpus.documents
        assert all(entry.file_name.endswith(".csv") for entry in corpus.manifest.documents)


class TestGeneratedDocumentsImport:
    """The corpus goes through the real strict parser, unmodified."""

    def test_every_document_imports(self, tmp_path: Path) -> None:
        """A corpus the parser refuses cannot be evaluated at all."""
        from app.benchmark.evaluator import EVALUATION_CLOCK, RECORD_TYPE_BY_FILE
        from app.benchmark.generator import SOURCE_SYSTEM
        from app.storage.database import create_database_engine, database_url_for

        corpus = generate(small_config())
        engine = create_database_engine(database_url_for(tmp_path / "import.sqlite"))
        create_schema(engine)

        try:
            with session_factory(engine)() as session:
                service = ImportService(session, now=EVALUATION_CLOCK)
                for entry in corpus.manifest.documents:
                    receipt = service.import_document(
                        corpus.documents[entry.file_name].encode("utf-8"),
                        source_system=SOURCE_SYSTEM,
                        record_type=RECORD_TYPE_BY_FILE[entry.file_name],
                        document_name=entry.file_name,
                    )
                    assert receipt.outcome is ImportOutcome.ACCEPTED
                    assert receipt.accepted_count == entry.row_count
                session.commit()
        finally:
            engine.dispose()

    def test_documents_match_the_strict_headers_exactly(self) -> None:
        """Rendered from the same source of truth the parser checks against."""
        from app.benchmark.evaluator import RECORD_TYPE_BY_FILE

        corpus = generate(small_config())
        for file_name, text in corpus.documents.items():
            header = next(csv.reader(io.StringIO(text)))
            assert tuple(header) == expected_headers(RECORD_TYPE_BY_FILE[file_name])

    def test_no_payment_event_has_a_zero_amount(self) -> None:
        """The contract refuses those, so the generator must never emit one."""
        corpus = generate(small_config(seed=31337))
        rows = list(csv.DictReader(io.StringIO(corpus.documents["payment_events.csv"])))

        assert rows
        assert all(int(row["amount_minor"]) > 0 for row in rows)

    @pytest.mark.parametrize("seed", [1, 7, 100, 20260701])
    def test_no_seed_produces_a_zero_amount(self, seed: int) -> None:
        """Across several seeds, since amounts are the part that varies."""
        corpus = generate(small_config(seed=seed))
        rows = list(csv.DictReader(io.StringIO(corpus.documents["payment_events.csv"])))

        assert all(int(row["amount_minor"]) > 0 for row in rows)


class TestWrittenCorpus:
    """What lands on disk."""

    def test_writing_produces_documents_and_a_manifest(self, tmp_path: Path) -> None:
        """Four files: three documents and the oracle."""
        written = write_corpus(generate(small_config()), tmp_path)

        assert {path.name for path in written} == {
            "payment_events.csv",
            "settlement_lines.csv",
            "payouts.csv",
            "manifest.json",
        }

    def test_the_written_manifest_parses(self, tmp_path: Path) -> None:
        """And carries the seed a reader would need to reproduce it."""
        write_corpus(generate(small_config(seed=515)), tmp_path)
        parsed = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

        assert parsed["seed"] == 515
        assert parsed["is_synthetic"] is True

    def test_document_hashes_match_what_was_written(self, tmp_path: Path) -> None:
        """So a manifest can be checked against the files beside it."""
        from app.ingestion.parsing import compute_document_hash

        corpus = generate(small_config())
        write_corpus(corpus, tmp_path)

        for entry in corpus.manifest.documents:
            written = (tmp_path / entry.file_name).read_bytes()
            assert compute_document_hash(written) == entry.sha256
