"""Export the domain contract as JSON Schema.

The Pydantic models are the source of truth. These files are generated from
them so that a reader, or a future evaluator written in another language, can
see the contract without running Python.

The generated files are committed. A test regenerates them and compares, so a
model change that is not reflected in the committed schema fails the build
rather than leaving a stale artifact in the repository.

Regenerate with::

    make schema
"""

import json
from pathlib import Path

from pydantic import BaseModel

from app.domain.decisions import EvidenceRef, ReconciliationDecision
from app.domain.facts import IdempotencyKey, SourceFact, SourceLocator
from app.domain.invariants import InvariantResult, InvariantSpec
from app.domain.lifecycle import (
    PaymentEvent,
    PaymentIdentity,
    PayoutBatch,
    SettlementLine,
)
from app.domain.money import Money, MoneyBreakdown
from app.domain.version import DOMAIN_SCHEMA_VERSION

#: Where the generated files live, relative to the repository root. The
#: directory is the compatibility boundary, not the exact version: a minor
#: release stays readable by a reader of v1, so it overwrites in place, while a
#: major release would write to a new directory and leave this one intact.
SCHEMA_DIR = Path("docs") / "schema" / "v1"

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every model that is part of the published contract. Internal helpers are not
#: listed, because publishing them would imply a stability promise this phase
#: does not make.
EXPORTED_MODELS: tuple[type[BaseModel], ...] = (
    EvidenceRef,
    IdempotencyKey,
    InvariantResult,
    InvariantSpec,
    Money,
    MoneyBreakdown,
    PaymentEvent,
    PaymentIdentity,
    PayoutBatch,
    ReconciliationDecision,
    SettlementLine,
    SourceFact,
    SourceLocator,
)


def schema_filename(model: type[BaseModel]) -> str:
    """Return the file name a model's schema is written to."""
    return f"{model.__name__}.schema.json"


def render_schema(model: type[BaseModel]) -> str:
    """Return the JSON Schema for one model, as it is written to disk.

    Keys are sorted and indentation is fixed so that regenerating an unchanged
    model produces a byte-identical file. Without that, the drift test would
    report noise instead of real changes.
    """
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["x-domain-contract-version"] = DOMAIN_SCHEMA_VERSION
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_schemas() -> dict[str, str]:
    """Return every exported schema, keyed by file name."""
    return {schema_filename(model): render_schema(model) for model in EXPORTED_MODELS}


def write_schemas(root: Path | None = None) -> list[Path]:
    """Write every exported schema to disk and return the paths written.

    Args:
        root: Repository root to write under. Defaults to the real one.

    Returns:
        The paths written, in sorted order.
    """
    base = (root or REPO_ROOT) / SCHEMA_DIR
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in sorted(build_schemas().items()):
        path = base / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    """Write the schema files and report what was written."""
    for path in write_schemas():
        print(path.relative_to(REPO_ROOT))


if __name__ == "__main__":  # pragma: no cover
    main()
