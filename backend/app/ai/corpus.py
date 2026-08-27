"""A generated corpus for evaluating bounded selection, with a private oracle.

The demo fixtures are an exact-reference lookup exercise: the provider is shown
the same strings the baseline matches on, so selecting correctly is string
equality and a report over them measures nothing about selection.

This corpus is built to be answerable and not trivial. Every case is a real
settlement line over real source facts, so the deterministic baseline computes
the truth exactly as it does anywhere else, and the difficulty is entirely in
what the provider is shown.

## Three separate things

**Canonical facts** are the source facts themselves. The baseline links by exact
reference over these, and that linking is the oracle.

**The presentation** is what a provider sees: a rendering of a reference, chosen
per record by the scenario. Reformatted, altered by one character, truncated so
two records look alike, or withheld.

**The expected action** is what a provider ought to do, which is not always to
select. Two families have no safe answer from what is shown, and abstaining on
those is correct even though the oracle knows the link.

The three are kept apart deliberately. The oracle is derived from canonical
facts and the manifest below; it is never read back from a presentation field. A
test corrupts a rendered reference and requires canonical truth to be unmoved.

## Opaque identifiers

Every generated identifier is a digest of the scenario seed and an index. A
provider-visible ID therefore carries no scenario name, no expected action, no
template name and no hint about which record is the answer. A leak scan over the
whole rendered input holds that.
"""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.ai.evaluation import ExpectedProviderAction
from app.ai.presentation import ReferenceStyle, render_reference
from app.domain.evidence import SourceFactIndex, build_fact_index
from app.domain.facts import (
    SourceFact,
    SourceLocator,
    SourceLocatorKind,
    SourceRecordType,
    SourceSystem,
    compute_payload_hash,
)
from app.domain.primitives import CanonicalPayload

CORPUS_VERSION = "1.0.0"
"""Version of the generator. Changes when a scenario's shape changes, so two
reports over corpora built by different versions are not compared."""

CORPUS_SEED = "settlement-witness-shadow-corpus"
"""The one seed every identifier derives from. Fixed, so the corpus is the same
corpus on every machine and in every run."""

BASE_TIME = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
PSP = SourceSystem.PSP_API


class ScenarioFamily(StrEnum):
    """What kind of difficulty a case presents.

    Private to the generator and the evaluator. Never rendered, never part of an
    identifier, and never present in a request.
    """

    EXACT_CONTROL = "EXACT_CONTROL"
    """Visible references are canonical. Proves ordinary selection still works,
    and gives the other families something to be measured against."""

    FORMAT_VARIANT = "FORMAT_VARIANT"
    """The line and its records show the same reference in different forms.
    Distractors carry genuinely different references, so selecting everything
    fails."""

    NEAR_NEIGHBOR = "NEAR_NEIGHBOR"
    """A distractor's reference differs from the line's by one digit. Linking it
    is wrong, and it is the wrong that looks most like right."""

    AMBIGUOUS_VISIBLE_REFERENCE = "AMBIGUOUS_VISIBLE_REFERENCE"
    """Two records render identically because the shown form is truncated. The
    oracle knows which one links; nothing shown does. Abstaining is safe."""

    WITHHELD_VISIBLE_REFERENCE = "WITHHELD_VISIBLE_REFERENCE"
    """The linked record's reference is not shown at all. Abstaining is safe."""

    MULTI_PAGE_TARGET = "MULTI_PAGE_TARGET"
    """True links spread across more than one candidate page, so a provider that
    answers only the first page cannot be exactly right."""


class Scenario(BaseModel):
    """One case, and everything about it that a provider must not see."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: ScenarioFamily
    settlement_line_id: str
    expected_action: ExpectedProviderAction
    linked_record_ids: tuple[str, ...]
    """The canonical answer, recorded by the generator. The evaluator's oracle
    is computed from the facts by the baseline; this is kept so a test can
    confirm the two agree, and never used in place of it."""

    styling: Mapping[str, ReferenceStyle]
    """How each record of this case is rendered."""


class ShadowCorpus(BaseModel):
    """The generated facts, and the manifest describing them.

    The manifest is private. It is passed to the evaluator and never to a
    request, and nothing in it reaches a provider.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    version: str
    facts: tuple[SourceFact, ...]
    scenarios: tuple[Scenario, ...]

    @property
    def index(self) -> SourceFactIndex:
        """Return the facts as an index, for building a snapshot."""
        return build_fact_index(self.facts)

    @property
    def styling(self) -> dict[str, ReferenceStyle]:
        """Return every record's rendering, across every scenario."""
        combined: dict[str, ReferenceStyle] = {}
        for scenario in self.scenarios:
            combined.update(scenario.styling)
        return combined

    @property
    def expected_actions(self) -> dict[str, ExpectedProviderAction]:
        """Return what each settlement line expects, by line ID."""
        return {
            scenario.settlement_line_id: scenario.expected_action for scenario in self.scenarios
        }

    def composition(self) -> dict[str, int]:
        """Return how many cases each family contributes, for the report."""
        counts: dict[str, int] = {}
        for scenario in self.scenarios:
            counts[scenario.family.value] = counts.get(scenario.family.value, 0) + 1
        return dict(sorted(counts.items()))


def opaque(*parts: object) -> str:
    """Return a deterministic opaque token.

    Every generated identifier goes through here, so nothing a provider sees
    encodes a scenario name, an expected action, or which record is the answer.
    Derived from the fixed seed, so the corpus is identical everywhere.
    """
    digest = hashlib.sha256(CORPUS_SEED.encode("utf-8"))
    for part in parts:
        digest.update(b"\x00")
        digest.update(str(part).encode("utf-8"))
    return digest.hexdigest()[:12]


def _reference(kind: str, case: int, index: int) -> str:
    """Return an opaque reference with a family segment and a tail.

    Three segments. Truncation drops the tail, so two references from one case
    render alike; a near miss shifts the last digit of the tail. Both need the
    tail to exist, which is why the shape is fixed here rather than left to each
    scenario.
    """
    return f"{kind}-{opaque(kind, case)}-{index:04d}"


def _fact(record_type: SourceRecordType, record_id: str, payload: CanonicalPayload) -> SourceFact:
    """Return one source fact with a computed payload hash."""
    return SourceFact(
        source_record_id=record_id,
        source_system=PSP,
        source_record_type=record_type,
        source_locator=SourceLocator(
            kind=SourceLocatorKind.API_RESOURCE, reference=f"/v1/{record_id}"
        ),
        provider_event_id=record_id,
        observed_at=BASE_TIME,
        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
        canonical_payload=payload,
        payload_hash=compute_payload_hash(payload),
    )


def _at(minutes: int) -> str:
    """Return an ISO timestamp offset from the anchor."""
    return (BASE_TIME + timedelta(minutes=minutes)).isoformat()


def _event(case: int, index: int, payment_id: str, *, minutes: int) -> SourceFact:
    """Return one payment event fact."""
    record_id = opaque("event", case, index)
    payload: CanonicalPayload = {
        "provider_event_id": record_id,
        "event_id": opaque("evt", case, index),
        "payment_id": payment_id,
        "merchant_id": opaque("merchant", case),
        "event_type": "CAPTURE",
        "amount_minor": 100_000,
        "currency": "INR",
        "occurred_at": _at(minutes),
    }
    return _fact(SourceRecordType.PAYMENT_EVENT, record_id, payload)


def _payout(case: int, payout_id: str) -> SourceFact:
    """Return one payout fact."""
    record_id = opaque("payout", case)
    payload: CanonicalPayload = {
        "provider_event_id": record_id,
        "payout_id": payout_id,
        "merchant_id": opaque("merchant", case),
        "net_minor": 97_640,
        "currency": "INR",
        "utr": opaque("utr", case),
        "occurred_at": _at(120),
    }
    return _fact(SourceRecordType.PAYOUT, record_id, payload)


def _line(case: int, payment_id: str, payout_id: str) -> SourceFact:
    """Return one settlement line fact."""
    record_id = opaque("line", case)
    payload: CanonicalPayload = {
        "provider_event_id": record_id,
        "settlement_line_id": opaque("sl", case),
        "payout_id": payout_id,
        "payment_id": payment_id,
        "gross_minor": 100_000,
        "fee_minor": 2_000,
        "tax_minor": 360,
        "adjustment_minor": 0,
        "net_minor": 97_640,
        "currency": "INR",
        "occurred_at": _at(60),
    }
    return _fact(SourceRecordType.SETTLEMENT_LINE, record_id, payload)


class _Case(BaseModel):
    """One scenario's facts and its manifest entry, before they are combined."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    facts: tuple[SourceFact, ...]
    scenario: Scenario


def _build_case(
    family: ScenarioFamily,
    case: int,
    *,
    linked_events: int = 1,
    distractor_events: int = 0,
    near_miss_distractors: int = 0,
    line_style: ReferenceStyle = ReferenceStyle.CANONICAL,
    linked_style: ReferenceStyle = ReferenceStyle.CANONICAL,
    distractor_style: ReferenceStyle = ReferenceStyle.CANONICAL,
    expected_action: ExpectedProviderAction = ExpectedProviderAction.SELECT_EXACTLY,
    ambiguous_twin: bool = False,
) -> _Case:
    """Build one case from a description of its difficulty.

    One builder for every family, because the families differ only in how many
    distractors there are and how each record is rendered. Writing six near
    identical builders would let them drift apart in ways that had nothing to do
    with the difficulty being tested.

    Args:
        family: Which difficulty this case presents.
        case: The case number, which every identifier derives from.
        linked_events: How many payment events genuinely link to the line.
        distractor_events: How many belong to an unrelated payment.
        near_miss_distractors: How many carry a reference one digit from the
            line's own.
        line_style: How the line's own reference is rendered.
        linked_style: How the linked records are rendered.
        distractor_style: How the distractors are rendered.
        expected_action: What a provider ought to do, given what it will see.
        ambiguous_twin: Whether to add a record whose rendered reference is
            indistinguishable from the linked one.

    Returns:
        The facts and the manifest entry.
    """
    payment_id = _reference("pay", case, 1)
    payout_id = _reference("po", case, 1)

    line = _line(case, payment_id, payout_id)
    payout = _payout(case, payout_id)
    facts: list[SourceFact] = [line, payout]
    styling: dict[str, ReferenceStyle] = {
        line.source_record_id: line_style,
        payout.source_record_id: linked_style,
    }
    linked: list[str] = [payout.source_record_id]

    for index in range(linked_events):
        event = _event(case, index, payment_id, minutes=index)
        facts.append(event)
        styling[event.source_record_id] = linked_style
        linked.append(event.source_record_id)

    # A distractor belongs to a different payment, so the baseline does not link
    # it however it happens to be rendered.
    for index in range(distractor_events):
        other = _reference("pay", case, 100 + index)
        event = _event(case, 500 + index, other, minutes=200 + index)
        facts.append(event)
        styling[event.source_record_id] = distractor_style

    # A near miss belongs to a payment whose reference is one digit from the
    # line's own, and is shown as it is. So it does not link, and it is the
    # wrong answer that looks most like the right one. Rendering a true link as
    # a near miss would be the opposite mistake: a record that does link, shown
    # under a reference that is not its own.
    near_payment = render_reference(payment_id, ReferenceStyle.NEAR_MISS)
    for index in range(near_miss_distractors):
        event = _event(case, 700 + index, str(near_payment), minutes=300 + index)
        facts.append(event)
        styling[event.source_record_id] = ReferenceStyle.CANONICAL

    # A twin shares the linked record's rendered form because both are truncated
    # to the same thing, while belonging to a different payment.
    if ambiguous_twin:
        twin_payment = _reference("pay", case, 2)
        twin = _event(case, 900, twin_payment, minutes=400)
        facts.append(twin)
        styling[twin.source_record_id] = ReferenceStyle.TRUNCATED

    return _Case(
        facts=tuple(facts),
        scenario=Scenario(
            family=family,
            settlement_line_id=str(line.canonical_payload["settlement_line_id"]),
            expected_action=expected_action,
            linked_record_ids=tuple(sorted(linked)),
            styling=styling,
        ),
    )


def _cases() -> tuple[_Case, ...]:
    """Return one case per family, in a fixed order.

    Each family is one case. More would make the rates smoother and would not
    make any of them mean anything different, and every denominator is reported
    so a reader can see how small the corpus is.
    """
    return (
        _build_case(
            ScenarioFamily.EXACT_CONTROL,
            case=1,
            distractor_events=2,
        ),
        _build_case(
            ScenarioFamily.FORMAT_VARIANT,
            case=2,
            line_style=ReferenceStyle.UPPERCASED,
            linked_style=ReferenceStyle.UNDERSCORED,
            distractor_style=ReferenceStyle.SPACED,
            distractor_events=2,
        ),
        _build_case(
            ScenarioFamily.NEAR_NEIGHBOR,
            case=3,
            near_miss_distractors=2,
            distractor_events=1,
        ),
        _build_case(
            ScenarioFamily.AMBIGUOUS_VISIBLE_REFERENCE,
            case=4,
            linked_style=ReferenceStyle.TRUNCATED,
            line_style=ReferenceStyle.TRUNCATED,
            ambiguous_twin=True,
            expected_action=ExpectedProviderAction.ABSTAIN,
        ),
        _build_case(
            ScenarioFamily.WITHHELD_VISIBLE_REFERENCE,
            case=5,
            linked_style=ReferenceStyle.WITHHELD,
            distractor_events=2,
            expected_action=ExpectedProviderAction.ABSTAIN,
        ),
        _build_case(
            ScenarioFamily.MULTI_PAGE_TARGET,
            case=6,
            linked_events=150,
            distractor_events=40,
        ),
    )


def build_corpus() -> ShadowCorpus:
    """Return the shadow corpus.

    Deterministic: no clock, no randomness, no environment. Two calls produce
    byte-identical facts and an identical manifest, and so does a call on
    another machine.
    """
    cases = _cases()
    return ShadowCorpus(
        version=CORPUS_VERSION,
        facts=tuple(
            sorted(
                (fact for case in cases for fact in case.facts),
                key=lambda fact: fact.source_record_id,
            )
        ),
        scenarios=tuple(case.scenario for case in cases),
    )


def rendered_input(requests: Sequence[object]) -> str:
    """Return everything a provider is shown, as one string.

    For the leak scan. If a scenario name, an expected action, a template name
    or a canonical answer appears anywhere a provider can read, it appears here.
    """
    return "\n".join(
        request.model_dump_json() if hasattr(request, "model_dump_json") else str(request)
        for request in requests
    )
