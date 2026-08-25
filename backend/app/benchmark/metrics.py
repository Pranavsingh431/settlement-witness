"""Metrics, and what they mean when there is nothing to measure.

Every rate here has a defined zero-denominator behaviour: it is null, not zero
and not one. A rate over no cases is not a good score or a bad one, it is an
absent measurement, and printing 0.0 or 1.0 would let an empty run look like a
result.
"""

from pydantic import BaseModel, ConfigDict


class Rate(BaseModel):
    """A fraction, with the counts it was computed from.

    Both parts are kept because a rate without its denominator cannot be
    checked, combined, or compared across runs of different sizes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    numerator: int
    denominator: int
    value: float | None
    """None when the denominator is zero: not measurable, rather than zero."""

    @classmethod
    def of(cls, numerator: int, denominator: int) -> "Rate":
        """Return a rate, or an unmeasurable one when there are no cases."""
        if denominator == 0:
            return cls(numerator=0, denominator=0, value=None)
        return cls(
            numerator=numerator,
            denominator=denominator,
            value=round(numerator / denominator, 6),
        )

    @property
    def is_measurable(self) -> bool:
        """Return True when at least one case contributed."""
        return self.denominator > 0
