"""Tests for the published JSON Schema artifacts.

The committed files must match what the models produce. Without this test a
model change would leave a stale schema in the repository, and the artifact
would quietly stop describing the contract it claims to describe.
"""

import json
from pathlib import Path

import pytest

from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.schema_export import (
    EXPORTED_MODELS,
    REPO_ROOT,
    SCHEMA_DIR,
    build_schemas,
    render_schema,
    schema_filename,
    write_schemas,
)


class TestCommittedSchemasAreCurrent:
    """The drift guard."""

    def test_every_exported_model_has_a_committed_file(self) -> None:
        """A new model must ship its schema in the same change."""
        for model in EXPORTED_MODELS:
            path = REPO_ROOT / SCHEMA_DIR / schema_filename(model)
            assert path.is_file(), f"missing committed schema for {model.__name__}"

    def test_committed_files_match_the_models(self) -> None:
        """Regenerating must produce exactly what is committed.

        If this fails, run `make schema` and commit the result.
        """
        for name, expected in build_schemas().items():
            path = REPO_ROOT / SCHEMA_DIR / name
            assert path.read_text(encoding="utf-8") == expected, (
                f"{name} is out of date; run 'make schema' and commit the result"
            )

    def test_no_extra_files_are_left_behind(self) -> None:
        """A removed model must not leave an orphaned schema."""
        committed = {path.name for path in (REPO_ROOT / SCHEMA_DIR).glob("*.schema.json")}
        assert committed == set(build_schemas())


class TestSchemaContent:
    """Properties the published contract must hold."""

    def test_no_schema_permits_a_floating_point_number(self) -> None:
        """The no-float money rule, checked against the published artifact."""
        for name, content in build_schemas().items():
            assert '"number"' not in content, f"{name} permits a float"

    def test_every_schema_records_the_contract_version(self) -> None:
        """A reader can tell which contract a file describes."""
        for content in build_schemas().values():
            assert json.loads(content)["x-domain-contract-version"] == DOMAIN_SCHEMA_VERSION

    def test_every_schema_declares_its_dialect(self) -> None:
        """A schema without a dialect is ambiguous to a validator."""
        for content in build_schemas().values():
            assert json.loads(content)["$schema"].startswith("https://json-schema.org/")

    def test_rendering_is_deterministic(self) -> None:
        """Regenerating an unchanged model must be byte identical."""
        for model in EXPORTED_MODELS:
            assert render_schema(model) == render_schema(model)


class TestWriteSchemas:
    """Writing is exercised against a temporary root, never the real one."""

    def test_write_creates_every_file(self, tmp_path: Path) -> None:
        """The writer produces the full set."""
        written = write_schemas(root=tmp_path)
        assert len(written) == len(EXPORTED_MODELS)
        for path in written:
            assert path.is_file()

    def test_written_content_matches_build(self, tmp_path: Path) -> None:
        """What is written is what the builder returned."""
        write_schemas(root=tmp_path)
        for name, expected in build_schemas().items():
            assert (tmp_path / SCHEMA_DIR / name).read_text(encoding="utf-8") == expected

    def test_writing_twice_is_stable(self, tmp_path: Path) -> None:
        """Running the generator again changes nothing."""
        write_schemas(root=tmp_path)
        first = {
            path.name: path.read_text(encoding="utf-8") for path in write_schemas(root=tmp_path)
        }
        second = {
            path.name: path.read_text(encoding="utf-8") for path in write_schemas(root=tmp_path)
        }
        assert first == second


class TestExportedSet:
    """The published set is deliberate, not incidental."""

    def test_the_decision_contract_is_published(self) -> None:
        """The most important model must be readable outside Python."""
        assert any(model.__name__ == "ReconciliationDecision" for model in EXPORTED_MODELS)

    def test_model_names_are_unique(self) -> None:
        """Two models cannot write to the same file."""
        names = [schema_filename(model) for model in EXPORTED_MODELS]
        assert len(names) == len(set(names))

    def test_the_real_schema_directory_is_inside_the_repository(self) -> None:
        """A generator that writes outside the repository would be a bug."""
        assert (REPO_ROOT / SCHEMA_DIR).is_relative_to(REPO_ROOT)


@pytest.mark.parametrize("model", EXPORTED_MODELS, ids=lambda model: model.__name__)
def test_each_schema_is_valid_json(model: type) -> None:
    """Every published file parses."""
    parsed = json.loads(render_schema(model))
    assert parsed["title"] == model.__name__


class TestMain:
    """The entry point behind `make schema`."""

    def test_main_writes_the_files_and_lists_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Redirected at a temporary root so the test never rewrites the repository."""
        from app import schema_export

        monkeypatch.setattr(schema_export, "REPO_ROOT", tmp_path)
        schema_export.main()

        printed = capsys.readouterr().out.strip().splitlines()
        assert len(printed) == len(EXPORTED_MODELS)
        for line in printed:
            assert (tmp_path / line).is_file()
