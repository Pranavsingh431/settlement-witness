"""Monetary values and the signed net formula.

Money is always an integer in the minor unit of its currency, for example paise
for INR or cents for USD. Floating point is never used, because binary floating
point cannot represent most decimal amounts exactly, and a reconciliation system
that compares amounts would then report differences that are artefacts of the
representation rather than real breaks.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict

from app.domain.primitives import (
    AmountMinor,
    CurrencyCode,
    NonNegativeAmountMinor,
)


class CurrencyMismatchError(ValueError):
    """Raised when an operation mixes two different currencies."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"cannot combine or order amounts in {left} and {right}")
        self.left = left
        self.right = right


def compute_net_minor(
    *,
    gross_minor: int,
    fee_minor: int,
    tax_minor: int,
    adjustment_minor: int,
) -> int:
    """Return the settled net for one line, in minor units.

    The signed formula used throughout the contract is::

        net_minor = gross_minor - fee_minor - tax_minor + adjustment_minor

    Fee and tax are held as non-negative magnitudes and subtracted here, so that
    a source which reports fees as positive numbers and a source which reports
    them as negative numbers cannot both look correct. Adjustment is signed,
    because it covers both credits and debits.

    Args:
        gross_minor: Amount captured before deductions. Never negative.
        fee_minor: Processing fee as a positive magnitude. Never negative.
        tax_minor: Tax on the fee as a positive magnitude. Never negative.
        adjustment_minor: Signed correction applied to this line.

    Returns:
        The net amount in minor units. It may be negative, for example when an
        adjustment claws back more than the line settled.
    """
    return gross_minor - fee_minor - tax_minor + adjustment_minor


class Money(BaseModel):
    """An amount in the minor unit of a single currency.

    Equality is structural, so two amounts in different currencies are simply
    not equal. Ordering and arithmetic raise instead, because an answer there
    would have to invent an exchange rate. This mirrors how Python treats naive
    and aware datetimes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount_minor: AmountMinor
    currency: CurrencyCode

    @classmethod
    def zero(cls, currency: str) -> Self:
        """Return a zero amount in the given currency."""
        return cls(amount_minor=0, currency=currency)

    @staticmethod
    def _require_money(value: object) -> "Money":
        """Return ``value`` as Money, or raise if it is something else."""
        if isinstance(value, Money):
            return value
        message = f"expected Money, got {type(value).__name__}"
        raise TypeError(message)

    def _require_same_currency(self, other: "Money") -> None:
        """Raise unless ``other`` is in this amount's currency."""
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def add(self, other: "Money") -> "Money":
        """Return the sum of two amounts in the same currency."""
        self._require_same_currency(other)
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def subtract(self, other: "Money") -> "Money":
        """Return the difference of two amounts in the same currency."""
        self._require_same_currency(other)
        return Money(amount_minor=self.amount_minor - other.amount_minor, currency=self.currency)

    def negated(self) -> "Money":
        """Return this amount with the opposite sign."""
        return Money(amount_minor=-self.amount_minor, currency=self.currency)

    def __add__(self, other: object) -> "Money":
        return self.add(self._require_money(other))

    def __sub__(self, other: object) -> "Money":
        return self.subtract(self._require_money(other))

    def __neg__(self) -> "Money":
        return self.negated()

    def __lt__(self, other: object) -> bool:
        money = self._require_money(other)
        self._require_same_currency(money)
        return self.amount_minor < money.amount_minor

    def __le__(self, other: object) -> bool:
        money = self._require_money(other)
        self._require_same_currency(money)
        return self.amount_minor <= money.amount_minor

    def __gt__(self, other: object) -> bool:
        money = self._require_money(other)
        self._require_same_currency(money)
        return self.amount_minor > money.amount_minor

    def __ge__(self, other: object) -> bool:
        money = self._require_money(other)
        self._require_same_currency(money)
        return self.amount_minor >= money.amount_minor


class MoneyBreakdown(BaseModel):
    """The parts of a settled amount, before the net is asserted by a source.

    This holds the components only. It does not hold the net, because the net a
    source declares is exactly what INV-002 exists to check. If the net were a
    computed property here, the check would compare a number against itself and
    always pass.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: CurrencyCode
    gross_minor: NonNegativeAmountMinor
    fee_minor: NonNegativeAmountMinor
    tax_minor: NonNegativeAmountMinor
    adjustment_minor: AmountMinor

    @property
    def expected_net_minor(self) -> int:
        """Return the net that the signed formula produces for these parts."""
        return compute_net_minor(
            gross_minor=self.gross_minor,
            fee_minor=self.fee_minor,
            tax_minor=self.tax_minor,
            adjustment_minor=self.adjustment_minor,
        )

    @property
    def expected_net(self) -> Money:
        """Return :attr:`expected_net_minor` as a Money value."""
        return Money(amount_minor=self.expected_net_minor, currency=self.currency)
