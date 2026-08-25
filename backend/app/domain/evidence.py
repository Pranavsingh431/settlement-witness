"""Evidence references and the source-fact verification boundary.

An evidence reference is a claim: "this decision rests on source record R, from
system S, whose payload hashed to H". Building the reference proves none of
that. It is a well formed citation, and a well formed citation can still be
wrong.

Verifying it means resolving the citation against the source facts that actually
exist. That needs the facts, and a Pydantic validator cannot go and find them.
So verification is a separate, explicit step that takes the facts as an argument.

Phase 1 has no persistence, so the caller supplies the facts directly. Phase 2
ingestion and storage will supply that index instead. The boundary does not move
when that happens: the same function will be called with a larger index. What
changes is who builds it, not what verification means.
"""

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from app.domain.codes import ExceptionCode, ReasonCode
from app.domain.facts import SourceFact, SourceSystem
from app.domain.primitives import PayloadHash, SourceRecordId


class EvidenceRef(BaseModel):
    """A pointer to one source fact that a decision rests on.

    Evidence is a reference to an observation, never a description of one. This
    model has no free text field and forbids extra keys, so a caller cannot
    attach a summary, a justification or model output to a piece of evidence.

    The payload hash is carried so the citation can be checked against the fact
    it names. A hash that no longer matches means either the citation was wrong
    or the fact was rewritten, and both are worth catching.

    Constructing this does not prove the fact exists. Only
    :func:`verify_evidence` does that, and only against facts it is given.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: SourceRecordId
    source_system: SourceSystem
    payload_hash: PayloadHash


class EvidenceOutcome(StrEnum):
    """The result of resolving one citation against the available facts."""

    VERIFIED = "VERIFIED"
    """A fact with this record ID exists, and its system and payload hash match."""

    FACT_NOT_FOUND = "FACT_NOT_FOUND"
    """No fact with this record ID was supplied. The citation resolves to nothing."""

    SOURCE_SYSTEM_MISMATCH = "SOURCE_SYSTEM_MISMATCH"
    """A fact with this record ID exists but came from a different system."""

    PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"
    """The fact exists and the system matches, but its content is not what was
    cited. The citation is stale, or the fact was rewritten."""


#: The reason code recorded for each way a citation can fail to resolve.
_REASON_BY_OUTCOME: dict[EvidenceOutcome, ReasonCode] = {
    EvidenceOutcome.FACT_NOT_FOUND: ReasonCode.EVIDENCE_FACT_NOT_FOUND,
    EvidenceOutcome.SOURCE_SYSTEM_MISMATCH: ReasonCode.EVIDENCE_SOURCE_SYSTEM_MISMATCH,
    EvidenceOutcome.PAYLOAD_HASH_MISMATCH: ReasonCode.EVIDENCE_PAYLOAD_HASH_MISMATCH,
}

#: The exception code each failure implies.
#:
#: A citation that resolves to nothing is an absence, so it means the evidence is
#: not there to judge on. A citation that resolves to something other than what
#: it claimed is a contradiction between the decision and the store, which is a
#: reference that does not map to what it says it maps to.
_EXCEPTION_BY_OUTCOME: dict[EvidenceOutcome, ExceptionCode] = {
    EvidenceOutcome.FACT_NOT_FOUND: ExceptionCode.INSUFFICIENT_EVIDENCE,
    EvidenceOutcome.SOURCE_SYSTEM_MISMATCH: ExceptionCode.UNMAPPED_REFERENCE,
    EvidenceOutcome.PAYLOAD_HASH_MISMATCH: ExceptionCode.UNMAPPED_REFERENCE,
}


class EvidenceVerification(BaseModel):
    """The recorded result of resolving one citation.

    A decision carries one of these per evidence reference. Together they are the
    certificate that the citations were checked, rather than an assurance that
    they were.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_record_id: SourceRecordId
    outcome: EvidenceOutcome
    reason_code: ReasonCode | None = None

    @property
    def is_verified(self) -> bool:
        """Return True when the citation resolved to the fact it claimed."""
        return self.outcome is EvidenceOutcome.VERIFIED


type SourceFactIndex = Mapping[SourceRecordId, SourceFact]
"""A read-only lookup from source record ID to the fact stored under it."""


def build_fact_index(facts: Iterable[SourceFact]) -> SourceFactIndex:
    """Return a read-only index of facts by source record ID.

    Args:
        facts: The facts available to verify against.

    Returns:
        A mapping that cannot be modified by its holder.

    Raises:
        ValueError: If two facts share a source record ID. Source facts are
            append-only, so one record ID names one fact. Two would mean the
            caller had already lost track of which is authoritative, and picking
            one here would hide that.
    """
    index: dict[SourceRecordId, SourceFact] = {}
    for fact in facts:
        existing = index.get(fact.source_record_id)
        if existing is not None and existing != fact:
            message = (
                f"two different facts share source_record_id {fact.source_record_id!r}; "
                "source facts are append-only, so one record ID names one fact"
            )
            raise ValueError(message)
        index[fact.source_record_id] = fact
    return MappingProxyType(index)


def _coerce_index(facts: SourceFactIndex | Iterable[SourceFact]) -> SourceFactIndex:
    """Return an index keyed by each fact's own source record ID.

    A caller may hand over any mapping, and a mapping key is just a label the
    caller chose. It can disagree with the fact stored under it, by mistake or
    otherwise. So the key is discarded and the index is rebuilt from the facts
    themselves, which are the only thing here that carries its own identity.

    The rule this enforces is small and worth stating plainly: this module never
    trusts a container key more than the fact inside it.

    Args:
        facts: A mapping whose values are facts, or any iterable of facts.

    Returns:
        A read-only index in which every key is the record ID its fact declares.
    """
    if isinstance(facts, Mapping):
        return build_fact_index(facts.values())
    return build_fact_index(facts)


def verify_against_index(reference: EvidenceRef, index: SourceFactIndex) -> EvidenceVerification:
    """Resolve one citation against an index that is already keyed by record ID.

    Three things must agree before a citation verifies: the fact's own record ID,
    its source system, and its payload hash.

    Checking the record ID looks redundant, because :func:`_coerce_index` has
    already rebuilt the index from the facts and every key therefore matches the
    fact under it. It is kept because the alternative is a verifier that would
    accept a fact purely on the strength of where it was filed. A fact whose own
    identity disagrees with the citation resolves to nothing, exactly as if it
    were absent, because for that citation it is.

    Args:
        reference: The citation to check.
        index: Facts keyed by the record ID each one declares.

    Returns:
        The verification result, naming the precise way it failed if it did.
    """
    fact = index.get(reference.source_record_id)

    if fact is None or fact.source_record_id != reference.source_record_id:
        outcome = EvidenceOutcome.FACT_NOT_FOUND
    elif fact.source_system is not reference.source_system:
        outcome = EvidenceOutcome.SOURCE_SYSTEM_MISMATCH
    elif fact.payload_hash != reference.payload_hash:
        outcome = EvidenceOutcome.PAYLOAD_HASH_MISMATCH
    else:
        outcome = EvidenceOutcome.VERIFIED

    return EvidenceVerification(
        source_record_id=reference.source_record_id,
        outcome=outcome,
        reason_code=_REASON_BY_OUTCOME.get(outcome),
    )


def verify_reference(
    reference: EvidenceRef, facts: SourceFactIndex | Iterable[SourceFact]
) -> EvidenceVerification:
    """Resolve one citation against the supplied facts.

    Args:
        reference: The citation to check.
        facts: The facts available, as a mapping or any iterable of facts. A
            mapping's keys are discarded and rebuilt from the facts.

    Returns:
        The verification result, naming the precise way it failed if it did.
    """
    return verify_against_index(reference, _coerce_index(facts))


def verify_evidence(
    evidence: Sequence[EvidenceRef], facts: SourceFactIndex | Iterable[SourceFact]
) -> tuple[EvidenceVerification, ...]:
    """Resolve every citation against the supplied facts.

    Pure: it reads the arguments, touches nothing else, and returns one result
    per reference in the order the references were given.
    """
    index = _coerce_index(facts)
    return tuple(verify_against_index(reference, index) for reference in evidence)


def exception_codes_for(
    evidence: Sequence[EvidenceRef], verification: Sequence[EvidenceVerification]
) -> tuple[ExceptionCode, ...]:
    """Return the exception codes that unresolved citations imply.

    A citation with no verification result at all counts as unresolved. Treating
    an unchecked citation as acceptable would let a decision skip the check
    simply by not recording it.

    Args:
        evidence: The citations a decision made.
        verification: The results recorded for them.

    Returns:
        The implied codes, deduplicated, in precedence-independent order.
    """
    checked = {result.source_record_id: result for result in verification}
    codes: list[ExceptionCode] = []

    for reference in evidence:
        result = checked.get(reference.source_record_id)
        if result is None:
            codes.append(ExceptionCode.INSUFFICIENT_EVIDENCE)
        elif not result.is_verified:
            codes.append(_EXCEPTION_BY_OUTCOME[result.outcome])

    seen: set[ExceptionCode] = set()
    unique: list[ExceptionCode] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique.append(code)
    return tuple(unique)


def all_verified(
    evidence: Sequence[EvidenceRef], verification: Sequence[EvidenceVerification]
) -> bool:
    """Return True when every citation resolved to the fact it claimed."""
    return not exception_codes_for(evidence, verification)
