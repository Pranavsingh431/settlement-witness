"""Tests for monetary values and the signed net formula."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.money import CurrencyMismatchError, Money, MoneyBreakdown, compute_net_minor
from tests.domain.conftest import make_breakdown, make_money


class TestNetFormula:
    """The signed formula is the one definition of a settled net."""

    def test_net_subtracts_fee_and_tax_and_adds_adjustment(self) -> None:
        """gross - fee - tax + adjustment, with adjustment signed."""
        assert (
            compute_net_minor(gross_minor=10_000, fee_minor=200, tax_minor=36, adjustment_minor=0)
            == 9_764
        )

    def test_a_positive_adjustment_increases_the_net(self) -> None:
        """An adjustment is added, so a credit raises what settles."""
        assert (
            compute_net_minor(gross_minor=10_000, fee_minor=200, tax_minor=36, adjustment_minor=50)
            == 9_814
        )

    def test_a_negative_adjustment_can_drive_the_net_below_zero(self) -> None:
        """A clawback larger than the line is representable, not an error."""
        assert (
            compute_net_minor(gross_minor=100, fee_minor=0, tax_minor=0, adjustment_minor=-500)
            == -400
        )

    def test_breakdown_exposes_the_same_number_as_the_function(self) -> None:
        """The model must not carry a second, drifting implementation."""
        breakdown = make_breakdown()
        assert breakdown.expected_net_minor == compute_net_minor(
            gross_minor=breakdown.gross_minor,
            fee_minor=breakdown.fee_minor,
            tax_minor=breakdown.tax_minor,
            adjustment_minor=breakdown.adjustment_minor,
        )

    def test_expected_net_carries_the_breakdown_currency(self) -> None:
        """The computed net is Money, not a bare integer."""
        assert make_breakdown(currency="USD").expected_net.currency == "USD"


class TestBreakdownConstraints:
    """Gross, fee and tax are magnitudes. Only adjustment is signed."""

    @pytest.mark.parametrize("field", ["gross_minor", "fee_minor", "tax_minor"])
    def test_negative_components_are_rejected(self, field: str) -> None:
        """A negative fee would make the sign convention ambiguous."""
        with pytest.raises(ValidationError):
            make_breakdown(**{field: -1})

    def test_a_negative_adjustment_is_accepted(self) -> None:
        """Adjustments cover both credits and debits."""
        assert make_breakdown(adjustment_minor=-250).adjustment_minor == -250

    def test_a_float_amount_is_rejected(self) -> None:
        """Money is never floating point, not even when the value is integral."""
        with pytest.raises(ValidationError):
            make_breakdown(gross_minor=100.0)

    def test_unknown_fields_are_rejected(self) -> None:
        """A typo must fail loudly rather than be silently ignored."""
        with pytest.raises(ValidationError):
            make_breakdown(net_minor=9_764)


class TestMoneyValidation:
    """Money is an integer in the minor unit of a valid currency code."""

    def test_a_float_amount_is_rejected(self) -> None:
        """The type refuses floats at construction."""
        with pytest.raises(ValidationError):
            Money(amount_minor=10.5, currency="INR")  # type: ignore[arg-type]

    def test_an_integral_float_is_still_rejected(self) -> None:
        """10.0 is not an integer here. Accepting it would invite 10.1 later."""
        with pytest.raises(ValidationError):
            Money(amount_minor=10.0, currency="INR")  # type: ignore[arg-type]

    @pytest.mark.parametrize("currency", ["inr", "IN", "INRR", "", "1NR"])
    def test_currency_must_be_three_uppercase_letters(self, currency: str) -> None:
        """The contract validates ISO 4217 shape."""
        with pytest.raises(ValidationError):
            Money(amount_minor=1, currency=currency)

    def test_a_negative_amount_is_allowed(self) -> None:
        """Reversals and clawbacks are real amounts."""
        assert Money(amount_minor=-500, currency="INR").amount_minor == -500

    def test_zero_builds_an_empty_amount(self) -> None:
        """A zero helper avoids a magic literal at every call site."""
        assert Money.zero("INR") == Money(amount_minor=0, currency="INR")

    def test_money_is_immutable(self) -> None:
        """A frozen amount cannot be edited after it is built."""
        with pytest.raises(ValidationError):
            make_money().amount_minor = 1


class TestMoneyArithmetic:
    """Arithmetic works within a currency and is refused across currencies."""

    def test_addition_within_a_currency(self) -> None:
        """Two amounts in one currency add."""
        assert make_money(100) + make_money(50) == make_money(150)

    def test_subtraction_within_a_currency(self) -> None:
        """Two amounts in one currency subtract."""
        assert make_money(100) - make_money(150) == make_money(-50)

    def test_negation_flips_the_sign(self) -> None:
        """Negation keeps the currency."""
        assert -make_money(100) == make_money(-100)

    @pytest.mark.parametrize("operation", ["add", "subtract"])
    def test_arithmetic_across_currencies_is_rejected(self, operation: str) -> None:
        """Combining currencies would need an exchange rate this layer must not invent."""
        left, right = make_money(100, "INR"), make_money(100, "USD")
        with pytest.raises(CurrencyMismatchError):
            getattr(left, operation)(right)

    def test_the_error_names_both_currencies(self) -> None:
        """A person reading the failure should not have to guess which two."""
        with pytest.raises(CurrencyMismatchError) as caught:
            make_money(1, "INR") + make_money(1, "EUR")
        assert caught.value.left == "INR"
        assert caught.value.right == "EUR"

    def test_arithmetic_with_a_non_money_value_is_rejected(self) -> None:
        """Adding a bare integer would lose the currency."""
        with pytest.raises(TypeError):
            make_money(100) + 100


class TestMoneyOrdering:
    """Ordering follows the same rule as arithmetic. Equality does not."""

    def test_ordering_within_a_currency(self) -> None:
        """All four comparisons work when the currency matches."""
        small, large = make_money(100), make_money(200)
        assert small < large
        assert small <= large
        assert large > small
        assert large >= small

    @pytest.mark.parametrize("operator", ["__lt__", "__le__", "__gt__", "__ge__"])
    def test_ordering_across_currencies_is_rejected(self, operator: str) -> None:
        """There is no true answer to whether 100 INR exceeds 100 USD."""
        left, right = make_money(100, "INR"), make_money(100, "USD")
        with pytest.raises(CurrencyMismatchError):
            getattr(left, operator)(right)

    def test_equality_across_currencies_is_false_rather_than_an_error(self) -> None:
        """Equality is structural, matching how Python treats naive and aware datetimes.

        Raising here would break dict lookups and containment checks for no gain,
        because False is already the honest answer.
        """
        assert make_money(100, "INR") != make_money(100, "USD")

    def test_money_is_not_equal_to_an_unrelated_type(self) -> None:
        """Comparing against another type is simply unequal."""
        assert make_money(100) != datetime(2026, 1, 1, tzinfo=UTC)


class TestBreakdownImmutability:
    """A breakdown is frozen so a check cannot alter what it is checking."""

    def test_breakdown_cannot_be_mutated(self) -> None:
        """Frozen means frozen."""
        with pytest.raises(ValidationError):
            MoneyBreakdown(
                currency="INR",
                gross_minor=1,
                fee_minor=0,
                tax_minor=0,
                adjustment_minor=0,
            ).gross_minor = 2
