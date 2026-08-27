"""The contract document must state the version the code actually declares.

Written after `docs/domain-contract.md` was found claiming `2.0.0` while
`DOMAIN_SCHEMA_VERSION` had been `5.0.0` for three major steps. Nothing caught
it because nothing was checking, and a document that names the wrong version is
worse than one that names none: a reader has no reason to doubt it.
"""

import re
from pathlib import Path

import pytest

from app.domain.version import DOMAIN_SCHEMA_VERSION

CONTRACT = Path(__file__).resolve().parents[2].parent / "docs" / "domain-contract.md"


@pytest.fixture(scope="module")
def contract_text() -> str:
    """Return the published contract document."""
    return CONTRACT.read_text(encoding="utf-8")


def test_the_document_exists_where_the_test_looks_for_it() -> None:
    """Otherwise the checks below would pass by reading nothing."""
    assert CONTRACT.is_file(), f"no contract document at {CONTRACT}"


def test_the_stated_version_matches_the_constant(contract_text: str) -> None:
    """The sentence that was wrong."""
    stated = re.search(r"`DOMAIN_SCHEMA_VERSION` is `([^`]+)`", contract_text)

    assert stated is not None, "the document no longer states the version"
    assert stated.group(1) == DOMAIN_SCHEMA_VERSION


def test_the_heading_matches_the_constant(contract_text: str) -> None:
    """The two places a reader looks first must agree with each other."""
    heading = re.match(r"# Domain contract, version ([^\s]+)", contract_text)

    assert heading is not None, "the document no longer names its version in the heading"
    assert heading.group(1) == DOMAIN_SCHEMA_VERSION


def test_the_version_table_records_the_current_version(contract_text: str) -> None:
    """A major step with no row would leave the history incomplete."""
    assert f"| {DOMAIN_SCHEMA_VERSION} |" in contract_text
