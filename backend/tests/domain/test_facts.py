"""Tests for append-only source facts and the idempotency contract."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.facts import (
    IngestionOutcome,
    SourceLocator,
    SourceLocatorKind,
    SourceSystem,
    classify_ingestion,
    compute_payload_hash,
)
from tests.domain.conftest import FIXED_TIME, make_fact, make_locator


class TestPayloadHash:
    """The digest is what makes duplicate detection meaningful."""

    def test_key_order_does_not_change_the_digest(self) -> None:
        """Two payloads that differ only in key order are the same payload."""
        assert compute_payload_hash({"a": 1, "b": 2}) == compute_payload_hash({"b": 2, "a": 1})

    def test_a_different_value_changes_the_digest(self) -> None:
        """A real difference must be visible."""
        assert compute_payload_hash({"a": 1}) != compute_payload_hash({"a": 2})

    def test_nesting_is_covered(self) -> None:
        """Sorting applies at every level, not just the top."""
        assert compute_payload_hash({"a": {"x": 1, "y": 2}}) == compute_payload_hash(
            {"a": {"y": 2, "x": 1}}
        )

    def test_non_ascii_content_is_handled(self) -> None:
        """Merchant names are not always ASCII."""
        assert len(compute_payload_hash({"name": "Kraków"})) == 64


class TestSourceFactValidation:
    """Derived fields are validated here. Source claims are left to invariants."""

    def test_a_valid_fact_builds(self) -> None:
        """The happy path works."""
        assert make_fact().source_record_id == "rec-1"

    def test_a_hash_that_disagrees_with_the_payload_is_rejected(self) -> None:
        """The hash is ours, not the source's, so a mismatch is incoherent."""
        with pytest.raises(ValidationError, match="payload_hash does not match"):
            make_fact(payload_hash="b" * 64)

    def test_a_malformed_hash_is_rejected(self) -> None:
        """The digest must look like a SHA-256 digest."""
        with pytest.raises(ValidationError):
            make_fact(payload_hash="not-a-digest")

    def test_a_naive_timestamp_is_rejected(self) -> None:
        """A naive timestamp cannot be ordered against an aware one."""
        with pytest.raises(ValidationError):
            make_fact(observed_at=datetime(2026, 8, 24, 12, 0, 0))

    def test_an_offset_timestamp_is_stored_as_the_same_instant_in_utc(self) -> None:
        """The offset a timestamp arrived with is presentation, not fact."""
        ist = timezone(timedelta(hours=5, minutes=30))
        fact = make_fact(occurred_at=datetime(2026, 8, 24, 5, 30, tzinfo=ist))
        assert fact.occurred_at == datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
        assert fact.occurred_at.tzinfo is UTC

    def test_a_float_in_the_payload_is_rejected(self) -> None:
        """Ingestion converts to minor units before a fact is built.

        Allowing a float here would let a rounding error enter the system and
        then be reported later as a real settlement difference.
        """
        with pytest.raises(ValidationError):
            make_fact(payload={"amount": 97.64})  # type: ignore[dict-item]

    def test_an_empty_identifier_is_rejected(self) -> None:
        """A blank identifier is not an identifier."""
        with pytest.raises(ValidationError):
            make_fact(source_record_id="")

    def test_a_padded_identifier_is_rejected(self) -> None:
        """Two records must not differ by invisible characters alone."""
        with pytest.raises(ValidationError):
            make_fact(source_record_id=" rec-1 ")

    def test_unknown_fields_are_rejected(self) -> None:
        """A fact carries what the contract says it carries."""
        with pytest.raises(ValidationError):
            make_fact(reviewer_note="looks fine to me")

    def test_a_fact_cannot_be_edited(self) -> None:
        """Append-only starts with the object itself being frozen."""
        with pytest.raises(ValidationError):
            make_fact().source_record_id = "rec-2"


class TestSourceLocator:
    """A locator points a person at the row that caused an exception."""

    def test_a_file_row_locator_needs_a_row_number(self) -> None:
        """Naming a file without the row makes a person search it by hand."""
        with pytest.raises(ValidationError, match="row_number is required"):
            SourceLocator(kind=SourceLocatorKind.FILE_ROW, reference="a.csv")

    def test_row_numbers_are_one_based(self) -> None:
        """Row zero would not match what a spreadsheet shows."""
        with pytest.raises(ValidationError, match="one-based"):
            make_locator(row_number=0)

    def test_an_api_locator_must_not_carry_a_row_number(self) -> None:
        """A row number on an API resource is meaningless and misleading."""
        with pytest.raises(ValidationError, match="only meaningful for FILE_ROW"):
            SourceLocator(
                kind=SourceLocatorKind.API_RESOURCE,
                reference="/v1/settlements/1",
                row_number=3,
            )

    def test_an_api_locator_without_a_row_number_is_valid(self) -> None:
        """The normal API case works."""
        locator = SourceLocator(kind=SourceLocatorKind.API_RESOURCE, reference="/v1/settlements/1")
        assert locator.row_number is None


class TestIdempotencyIdentity:
    """Identity is the source system plus the provider event ID."""

    def test_identity_combines_system_and_provider_event_id(self) -> None:
        """Both parts are present."""
        key = make_fact().idempotency_key
        assert key.source_system is SourceSystem.PSP_API
        assert key.provider_event_id == "evt-1"

    def test_the_same_provider_event_from_two_systems_is_two_identities(self) -> None:
        """Two systems can disagree, and that disagreement is worth keeping."""
        api = make_fact(source_system=SourceSystem.PSP_API)
        hook = make_fact(source_system=SourceSystem.PSP_WEBHOOK)
        assert api.idempotency_key != hook.idempotency_key


class TestIngestionClassification:
    """Duplicate delivery is normal. Contradiction is not."""

    def test_no_stored_fact_means_new(self) -> None:
        """First sight of a record."""
        assert classify_ingestion(make_fact(), None) is IngestionOutcome.NEW

    def test_an_identical_replay_is_a_harmless_no_op(self) -> None:
        """Providers resend webhooks and files get loaded twice."""
        stored = make_fact()
        replay = make_fact(source_record_id="rec-99")
        assert classify_ingestion(replay, stored) is IngestionOutcome.DUPLICATE_NO_OP

    def test_the_same_identity_with_a_different_payload_conflicts(self) -> None:
        """One of the two observations is wrong and neither may be preferred."""
        stored = make_fact(payload={"amount_minor": 9_764})
        changed = make_fact(payload={"amount_minor": 9_999})
        assert classify_ingestion(changed, stored) is IngestionOutcome.DUPLICATE_CONFLICT

    def test_comparing_two_unrelated_facts_is_refused(self) -> None:
        """Answering would produce a duplicate verdict about different records."""
        stored = make_fact(provider_event_id="evt-1")
        other = make_fact(provider_event_id="evt-2")
        with pytest.raises(ValueError, match="different idempotency key"):
            classify_ingestion(other, stored)


class TestObservedAndOccurredAreSeparate:
    """Out of order arrival is expected, so both times are recorded."""

    def test_a_fact_can_be_observed_long_after_it_occurred(self) -> None:
        """A late webhook is still a valid fact."""
        fact = make_fact(occurred_at=FIXED_TIME, observed_at=FIXED_TIME + timedelta(days=3))
        assert fact.observed_at > fact.occurred_at
