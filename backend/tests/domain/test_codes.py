"""Tests for the exception taxonomy and its precedence order."""

import pytest

from app.domain.codes import (
    EXCEPTION_PRECEDENCE,
    ExceptionCode,
    highest_precedence,
    precedence_rank,
)
from app.domain.decisions import STATUS_BY_EXCEPTION_CODE, DecisionStatus

REQUIRED_CODES = frozenset(
    {
        "MISSING_PAYMENT",
        "MISSING_SETTLEMENT",
        "AMOUNT_MISMATCH",
        "FEE_MISMATCH",
        "CURRENCY_MISMATCH",
        "DUPLICATE_CONFLICT",
        "OUT_OF_ORDER_EVENT",
        "PARTIAL_REFUND",
        "TIMING_PENDING",
        "UNMAPPED_REFERENCE",
        "MALFORMED_RECORD",
        "UNSUPPORTED_STATE",
        "INSUFFICIENT_EVIDENCE",
    }
)


class TestTaxonomy:
    """The codes are stable identifiers, not adjustable labels."""

    def test_every_required_code_exists(self) -> None:
        """The contract names these thirteen."""
        assert {code.value for code in ExceptionCode} == REQUIRED_CODES

    def test_each_code_value_matches_its_name(self) -> None:
        """A code read from storage is the name a person looks up."""
        for code in ExceptionCode:
            assert code.value == code.name


class TestPrecedence:
    """Precedence decides which code speaks when several apply."""

    def test_precedence_covers_every_code_exactly_once(self) -> None:
        """A code missing from the order would have undefined behaviour."""
        assert len(EXCEPTION_PRECEDENCE) == len(set(EXCEPTION_PRECEDENCE))
        assert set(EXCEPTION_PRECEDENCE) == set(ExceptionCode)

    def test_malformed_data_outranks_every_interpretation_of_it(self) -> None:
        """A broken record must never be reported as a clean match."""
        for code in ExceptionCode:
            if code is not ExceptionCode.MALFORMED_RECORD:
                assert precedence_rank(ExceptionCode.MALFORMED_RECORD) < precedence_rank(code)

    def test_conflicting_source_data_outranks_amount_differences(self) -> None:
        """If the source contradicts itself, comparing amounts is premature."""
        assert precedence_rank(ExceptionCode.DUPLICATE_CONFLICT) < precedence_rank(
            ExceptionCode.AMOUNT_MISMATCH
        )

    def test_timing_pending_yields_to_everything(self) -> None:
        """Being late is the weakest signal, because nothing is wrong yet."""
        for code in ExceptionCode:
            if code is not ExceptionCode.TIMING_PENDING:
                assert precedence_rank(code) < precedence_rank(ExceptionCode.TIMING_PENDING)

    def test_insufficient_evidence_outranks_only_timing(self) -> None:
        """Missing evidence is not a mismatch, so a real mismatch wins.

        It still beats lateness, because not knowing is a stronger statement
        than merely waiting.
        """
        assert precedence_rank(ExceptionCode.INSUFFICIENT_EVIDENCE) < precedence_rank(
            ExceptionCode.TIMING_PENDING
        )
        assert precedence_rank(ExceptionCode.AMOUNT_MISMATCH) < precedence_rank(
            ExceptionCode.INSUFFICIENT_EVIDENCE
        )

    @pytest.mark.parametrize(
        ("codes", "expected"),
        [
            (
                (ExceptionCode.TIMING_PENDING, ExceptionCode.MALFORMED_RECORD),
                ExceptionCode.MALFORMED_RECORD,
            ),
            (
                (ExceptionCode.FEE_MISMATCH, ExceptionCode.AMOUNT_MISMATCH),
                ExceptionCode.AMOUNT_MISMATCH,
            ),
            ((ExceptionCode.TIMING_PENDING,), ExceptionCode.TIMING_PENDING),
        ],
    )
    def test_highest_precedence_picks_the_winner(
        self, codes: tuple[ExceptionCode, ...], expected: ExceptionCode
    ) -> None:
        """The strongest code decides the outcome."""
        assert highest_precedence(codes) is expected

    def test_no_codes_means_no_winner(self) -> None:
        """An empty input has no deciding code, which is not an error."""
        assert highest_precedence(()) is None

    def test_order_of_input_does_not_change_the_winner(self) -> None:
        """Precedence is a property of the codes, not of arrival order."""
        codes = [ExceptionCode.AMOUNT_MISMATCH, ExceptionCode.MALFORMED_RECORD]
        assert highest_precedence(codes) is highest_precedence(list(reversed(codes)))


class TestStatusMapping:
    """Only two codes carry a status other than EXCEPTION."""

    def test_timing_pending_maps_to_pending(self) -> None:
        """Late is not broken."""
        assert STATUS_BY_EXCEPTION_CODE[ExceptionCode.TIMING_PENDING] is DecisionStatus.PENDING

    def test_insufficient_evidence_maps_to_its_own_status(self) -> None:
        """Abstention is not an exception."""
        assert (
            STATUS_BY_EXCEPTION_CODE[ExceptionCode.INSUFFICIENT_EVIDENCE]
            is DecisionStatus.INSUFFICIENT_EVIDENCE
        )

    def test_every_other_code_is_an_exception(self) -> None:
        """The default is the honest one."""
        special = {ExceptionCode.TIMING_PENDING, ExceptionCode.INSUFFICIENT_EVIDENCE}
        for code in ExceptionCode:
            if code not in special:
                assert code not in STATUS_BY_EXCEPTION_CODE
