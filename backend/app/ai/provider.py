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
