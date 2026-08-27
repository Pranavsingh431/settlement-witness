"""The seam a model would sit behind, and the fake that stands in for one.

**No hosted model is called anywhere in this phase.** There is no API key, no
provider SDK, and no network access. The only implementation here is a
deterministic fake whose behaviour is chosen by the caller, and every number in
the shadow report comes from it. That is a measurement of the boundary, not of a
model.

The interface is narrow on purpose. A provider receives one request and returns
one payload. It gets no session, no history, no way to ask a second question and
no way to reach anything the request did not carry.

Every way of failing is a typed outcome. A provider that raises, times out or
returns nothing produces a `ProviderFailure`, and the caller records that. It is
never retried into a different answer and never repaired into a valid one: a
provider that failed did not answer, and pretending otherwise would put a value
in the report that nothing produced.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.ai.candidates import LinkProposalRequest
from app.ai.presentation import equivalent
from app.ai.proposals import ProposalOutcome, ProviderIdentity


class FailureKind(StrEnum):
    """How a provider failed to answer."""

    TIMED_OUT = "TIMED_OUT"
    RAISED = "RAISED"
    RETURNED_NOTHING = "RETURNED_NOTHING"


class ProviderFailure(BaseModel):
    """A provider that did not answer, and how.

    Carries the kind and nothing else. No provider text, because a message
    composed by the thing that failed is not something to store or display next
    to a reconciliation result. No identity either: which provider failed is
    something the caller knows, and reading it from the failure would let a
    provider report its own failure against another name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FailureKind


type ProviderResult = object | ProviderFailure
"""Either a raw payload to be parsed and validated, or a typed failure.

Deliberately `object` rather than a proposal type. What a real provider returns
is untrusted text or JSON, and typing it as a proposal here would suggest
somebody had already checked it."""


class LinkProposalProvider(Protocol):
    """Anything that can answer one link question.

    One method, one request, one result. A real implementation would put a
    model behind this; nothing in this phase does.
    """

    @property
    def identity(self) -> ProviderIdentity:
        """Return which provider and version this is, for the audit trail."""
        ...

    def propose(self, request: LinkProposalRequest) -> ProviderResult:
        """Return a payload answering one request, or a typed failure."""
        ...


type Behaviour = Callable[[LinkProposalRequest], ProviderResult]
"""How a fake provider answers one request."""


class FixtureProvider:
    """A provider whose answers are written by the test that uses it.

    Deterministic: the same request always produces the same payload, so a
    shadow report is reproducible byte for byte. That is what lets the report be
    compared across runs at all, and it is why every proposal ID is derived
    rather than generated.
    """

    def __init__(self, behaviour: Behaviour, *, name: str = "fixture", version: str = "1") -> None:
        self._behaviour = behaviour
        self._identity = ProviderIdentity(name=name, version=version)

    @property
    def identity(self) -> ProviderIdentity:
        """Return which fake this is."""
        return self._identity

    def propose(self, request: LinkProposalRequest) -> ProviderResult:
        """Return whatever this fake was told to return for this request."""
        return self._behaviour(request)


def _payload(outcome: ProposalOutcome, selected: tuple[str, ...]) -> Mapping[str, object]:
    """Return a well-formed selection payload.

    Two keys, because that is the whole of what a provider may return. It names
    no line, no snapshot and no identity: the server writes those from what it
    already knows when it binds the answer to the question.
    """
    return {"outcome": outcome.value, "selected_source_record_ids": list(selected)}


def selecting(chooser: Callable[[LinkProposalRequest], tuple[str, ...]]) -> Behaviour:
    """Return a behaviour that selects whatever the chooser picks.

    Abstains when the chooser picks nothing, because a proposal with no records
    is a contradiction and the honest way to say "none of these" is an
    abstention.
    """

    def behave(request: LinkProposalRequest) -> ProviderResult:
        selected = chooser(request)
        outcome = ProposalOutcome.PROPOSE if selected else ProposalOutcome.ABSTAIN
        return _payload(outcome, selected)

    return behave


def always_abstains() -> Behaviour:
    """Return a behaviour that never selects anything."""
    return selecting(lambda _request: ())


def selects_everything() -> Behaviour:
    """Return a behaviour that selects the whole candidate set.

    The control that proves broad guessing is not rewarded. It scores perfect
    recall and is caught by precision, exact-set accuracy and the false-link
    rate, which is the reason those are reported separately.
    """
    return selecting(
        lambda request: tuple(
            sorted(candidate.source_record_id for candidate in request.candidates)
        )
    )


def fails_with(kind: FailureKind) -> Behaviour:
    """Return a behaviour that reports a typed failure.

    The failure carries only its kind. Which provider failed is read from the
    provider object by whatever is recording the failure, not taken from the
    failure itself.
    """
    return lambda _request: ProviderFailure(kind=kind)


def returns(payload: object) -> Behaviour:
    """Return a behaviour that returns one payload, whatever was asked.

    For the adversarial cases: malformed text, unknown fields, a stale
    fingerprint, another line's IDs, and the rest.
    """
    return lambda _request: payload


def matching_visible_references() -> Behaviour:
    """Return a behaviour that selects by reading the rendered references.

    A deterministic stand-in for a provider that reads what it is shown and
    behaves sensibly. It is not a model and does not pretend to be one: it does
    exactly three things, and each is something a careful reader would do.

    It selects a candidate whose rendered reference is equivalent to the line's,
    ignoring case and separators, because those are formatting differences a
    reader should see through. It selects nothing when the line's own reference
    is not shown, because there is nothing to match against. And it abstains
    when the reference it was given is less specific than the ones it is
    matching against, because anything matching a coarser reference might be one
    of several and choosing would be a guess presented as a link.

    That last rule is why the ambiguous and withheld families exist. A provider
    that always selects its best match would link the wrong record on one and an
    unfounded record on the other, and the safe-abstention metrics are what
    report the difference.
    """

    def behave(request: LinkProposalRequest) -> ProviderResult:
        payment = request.subject_payment_id
        payout = request.subject_payout_id

        by_payment = [
            candidate.source_record_id
            for candidate in request.candidates
            if equivalent(candidate.payment_id, payment)
        ]
        by_payout = [
            candidate.source_record_id
            for candidate in request.candidates
            if equivalent(candidate.payout_id, payout)
        ]

        # What was given to match on is coarser than the things being matched,
        # so anything it matches might be one of several. Selecting would be a
        # guess presented as a link.
        if _shown_too_coarsely(request, payment):
            return _payload(ProposalOutcome.ABSTAIN, ())

        selected = tuple(sorted(set(by_payment) | set(by_payout)))
        outcome = ProposalOutcome.PROPOSE if selected else ProposalOutcome.ABSTAIN
        return _payload(outcome, selected)

    return behave


def _shown_too_coarsely(request: LinkProposalRequest, payment: str | None) -> bool:
    """Return whether the line's reference is less specific than an identifier.

    A reference on this corpus has three segments. A truncated one has two, so
    it names a family of payments rather than a payment, and anything matching
    it might be any member of that family. Selecting then means picking one and
    presenting the guess as a link.

    Detected by comparing the line's shown reference against the candidates'.
    That is information a provider actually has: it can see that what it was
    given to match on is coarser than the things it is matching against, without
    knowing anything canonical. A rule reading the private oracle would not be a
    provider behaviour at all.
    """
    if payment is None:
        return False
    widths = [
        candidate.payment_id.count("-") + candidate.payment_id.count("_")
        for candidate in request.candidates
        if candidate.payment_id is not None
    ]
    if not widths:
        return False
    subject_width = payment.count("-") + payment.count("_") + payment.count(" ")
    return subject_width < max(widths)
