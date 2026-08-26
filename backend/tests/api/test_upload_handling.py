"""Tests for the pieces the import endpoint is assembled from.

Tested directly, rather than only through the endpoint, because two of them
refuse things that a well behaved HTTP client cannot send. A file part with no
name at all is read by Starlette as a plain form field and rejected before any
of this code runs, and a receipt whose counts disagree with its rows cannot be
produced by the service that writes one. Both are still worth holding, because
the reason they cannot happen today is a detail of something else.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.schemas import ImportReceiptView, RowOutcomeView
from app.api.uploads import MAX_DOCUMENT_NAME, UNNAMED_DOCUMENT, safe_document_name
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.receipts import ImportOutcome, RowOutcome


class TestNamingAnUploadedDocument:
    """A file name is hostile text until it has been reduced to a label."""

    def test_a_plain_name_is_kept(self) -> None:
        """Nothing is done to a name that was already fine."""
        assert safe_document_name("payouts.csv") == "payouts.csv"

    def test_no_name_at_all_falls_back(self) -> None:
        """Defensive: Starlette types the field as optional, so this is reachable."""
        assert safe_document_name(None) == UNNAMED_DOCUMENT

    @pytest.mark.parametrize("raw", ["", "   ", ".", "..", "../", "/", "\\"])
    def test_a_name_with_nothing_usable_falls_back(self, raw: str) -> None:
        """Deterministic, so the fallback is one value and not a guess."""
        assert safe_document_name(raw) == UNNAMED_DOCUMENT

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/etc/passwd", "passwd"),
            ("../../secrets.csv", "secrets.csv"),
            (r"C:\Windows\System32\hosts", "hosts"),
            ("mixed/paths\\here.csv", "here.csv"),
        ],
    )
    def test_directory_components_are_dropped(self, raw: str, expected: str) -> None:
        """Both separators, because a client may be on either kind of machine."""
        assert safe_document_name(raw) == expected

    def test_control_characters_are_dropped(self) -> None:
        """A receipt gets printed, and a name may not rewrite the screen."""
        assert safe_document_name("re\x1b[2Jport\x00.csv") == "re[2Jport.csv"

    def test_a_long_name_is_shortened_to_the_stored_width(self) -> None:
        """Shortened here rather than refused by the column after the import."""
        assert len(safe_document_name("x" * 5000)) == MAX_DOCUMENT_NAME

    def test_the_fallback_is_a_constant_and_not_derived_from_content(self) -> None:
        """Two unnamed uploads are labelled the same.

        Deriving the label from the document would make it differ exactly when
        the identity differs, which is what an identifier looks like, and the
        document name is explicitly not one.
        """
        assert safe_document_name(None) == safe_document_name("../")


def _view(**overrides: object) -> ImportReceiptView:
    """Build a receipt view, so a test can change one field of a valid one."""
    fields: dict[str, object] = {
        "receipt_id": "r-1",
        "document_hash": "a" * 64,
        "document_name": "payouts.csv",
        "source_system": SourceSystem.PSP_API,
        "source_record_type": SourceRecordType.PAYOUT,
        "parser_version": "3.0.0",
        "received_at": datetime(2026, 8, 26, tzinfo=UTC),
        "outcome": ImportOutcome.ACCEPTED,
        "row_count": 1,
        "accepted_count": 1,
        "duplicate_count": 0,
        "conflict_count": 0,
        "rejected_count": 0,
        "not_applied_count": 0,
        "wrote_facts": True,
        "failure_detail": None,
        "row_outcomes": [
            RowOutcomeView(
                row_number=2,
                outcome=RowOutcome.ACCEPTED,
                source_record_id="hash:PSP_API:PAYOUT:2",
                code=None,
                detail=None,
            )
        ],
    }
    return ImportReceiptView(**(fields | overrides))  # type: ignore[arg-type]


class TestASummaryMayNotDisagreeWithItsRows:
    """A summary that can drift from the list beneath it is worse than none.

    A reader checks the cheap number, not the long list. These are the same
    values from the same receipt, so a disagreement means something rebuilt one
    of them wrongly, and serving it would put a false count into an audit trail.
    """

    def test_a_consistent_receipt_is_accepted(self) -> None:
        """The baseline the rest of these change one field of."""
        assert _view().accepted_count == 1

    @pytest.mark.parametrize(
        "field",
        [
            "row_count",
            "accepted_count",
            "duplicate_count",
            "conflict_count",
            "rejected_count",
            "not_applied_count",
        ],
    )
    def test_a_count_that_does_not_match_the_rows_is_refused(self, field: str) -> None:
        """Every count, not only the total."""
        with pytest.raises(ValidationError, match="disagree with its row outcomes"):
            _view(**{field: 99})

    def test_a_count_of_the_wrong_outcome_is_refused(self) -> None:
        """Counting an accepted row as a duplicate is still a disagreement."""
        with pytest.raises(ValidationError, match="disagree with its row outcomes"):
            _view(accepted_count=0, duplicate_count=1)

    def test_claiming_facts_were_written_when_none_were_is_refused(self) -> None:
        """`wrote_facts` is derived, so it cannot become a second opinion."""
        with pytest.raises(ValidationError, match="wrote_facts is True"):
            _view(row_count=0, accepted_count=0, wrote_facts=True, row_outcomes=[])

    def test_denying_facts_were_written_when_some_were_is_refused(self) -> None:
        """The same rule in the other direction."""
        with pytest.raises(ValidationError, match="wrote_facts is False"):
            _view(wrote_facts=False)

    def test_a_receipt_with_no_rows_is_consistent(self) -> None:
        """A document that could not be read at all has no rows to count."""
        empty = _view(
            outcome=ImportOutcome.REJECTED_INVALID,
            row_count=0,
            accepted_count=0,
            wrote_facts=False,
            failure_detail="MISSING_HEADER: document is empty",
            row_outcomes=[],
        )

        assert empty.row_outcomes == []
        assert empty.wrote_facts is False
