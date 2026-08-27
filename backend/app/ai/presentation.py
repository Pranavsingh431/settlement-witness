"""How a reference is shown to a provider, as against what it actually is.

The deterministic baseline links by exact reference: a settlement line links the
payment events whose payment ID equals its own. If a provider were shown those
same canonical strings, selecting correctly would be string equality and the
evaluation would measure nothing.

So what a provider sees is a rendering of a reference, not the reference. A
style decides how, and the styles are chosen to produce the four difficulties
worth measuring:

- an equivalent form, differing only in punctuation or case, which a reader
  should still recognise;
- a near neighbour, differing in one character, which a reader should not;
- a truncated form, which two different references can share, so nothing shown
  distinguishes them;
- an absent form, where the reference is not shown at all.

Canonical values are never modified. Rendering happens on the way to a request
and nowhere else, so the facts, the baseline and the private oracle all continue
to work in canonical terms. A test proves that corrupting a rendered value
leaves canonical truth untouched.
"""

from enum import StrEnum


class ReferenceStyle(StrEnum):
    """How one reference is written when it is shown."""

    CANONICAL = "CANONICAL"
    """As it is. The control."""

    DASHED = "DASHED"
    """Separators normalised to dashes."""

    UNDERSCORED = "UNDERSCORED"
    """Separators normalised to underscores."""

    UPPERCASED = "UPPERCASED"
    """Upper case, separators kept."""

    SPACED = "SPACED"
    """Separators replaced by spaces, which is the least machine-like form a
    real export tends to produce."""

    NEAR_MISS = "NEAR_MISS"
    """One character changed. Not a formatting difference: a different
    reference, rendered to look like a plausible neighbour of the real one."""

    TRUNCATED = "TRUNCATED"
    """The last segment removed, so two references can render alike.

    Lossy on purpose. This is what makes a case ambiguous from what is shown
    while the private oracle still knows which record links."""

    WITHHELD = "WITHHELD"
    """Not shown at all."""


_SEPARATORS = "-_ "


def render_reference(value: str, style: ReferenceStyle) -> str | None:
    """Return how one reference is written under a style.

    Args:
        value: The canonical reference.
        style: How to write it.

    Returns:
        The rendered form, or None when the style withholds it.
    """
    if style is ReferenceStyle.WITHHELD:
        return None
    if style is ReferenceStyle.CANONICAL:
        return value

    body = value
    for separator in _SEPARATORS:
        body = body.replace(separator, "\x00")

    if style is ReferenceStyle.DASHED:
        return body.replace("\x00", "-")
    if style is ReferenceStyle.UNDERSCORED:
        return body.replace("\x00", "_")
    if style is ReferenceStyle.SPACED:
        return body.replace("\x00", " ")
    if style is ReferenceStyle.UPPERCASED:
        return value.upper()
    if style is ReferenceStyle.TRUNCATED:
        # Drop the last segment, which is where the distinguishing part of a
        # reference lives. Two references differing only there render
        # identically, which is what makes a case ambiguous from what is shown.
        head, _, tail = body.rpartition("\x00")
        return (head or tail).replace("\x00", "-")

    # NEAR_MISS: change the last digit, or the last character when there is
    # none, so the result is a plausible neighbour rather than obvious noise.
    for index in range(len(value) - 1, -1, -1):
        if value[index].isdigit():
            shifted = str((int(value[index]) + 1) % 10)
            return value[:index] + shifted + value[index + 1 :]
    return value[:-1] + ("x" if value[-1:] != "x" else "y")


def equivalent(first: str | None, second: str | None) -> bool:
    """Return whether two rendered references describe the same thing.

    Case and separators are ignored, because those are the differences the
    format styles introduce and a reader is expected to see through. Nothing
    else is: a one-character difference is a different reference, and an absent
    reference matches nothing at all.

    Used by the fixture matcher, which stands in for a provider that reads the
    rendered fields sensibly. It is not used by the oracle, which works in
    canonical terms.
    """
    if first is None or second is None:
        return False
    return _flatten(first) == _flatten(second)


def _flatten(value: str) -> str:
    """Return a reference with case and separators removed."""
    body = value.casefold()
    for separator in _SEPARATORS:
        body = body.replace(separator, "")
    return body
