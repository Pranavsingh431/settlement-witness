"""Checking a proposal against the exact question that produced it.

Everything a provider returns is untrusted, including a proposal that parses.
The shape rules in `app.ai.proposals` say a selection is internally coherent;
they say nothing about whether it answers the right question about the right
facts. That is here.

A rejection is an AI-proposal failure and nothing else. It is not a
reconciliation exception, it does not become a decision, and it changes no fact,
receipt, run or decision. There is no repair step and no retry: a provider that
returned something invalid is told so, and the caller records an abstention or a
failure rather than a quietly corrected answer. Repairing model output would
mean the thing finally used was partly composed here and partly there, and
nobody could say which.

A provider supplies an outcome and a list of IDs. Which line was asked about,
which snapshot the question was against, and which provider answered are written
by the server from what it already holds. So a response cannot name a different
line, claim a different snapshot, or sign itself as another provider: there is no
field for any of it, and one that appears is refused as an extra.

When a selection is valid, `evidence_for` builds the evidence references by
loading each selected fact and reading its real record ID, source system and
payload hash. The provider never supplies a hash and never chooses one. It named
records; the hashes are a fact about those records that deterministic code looks
up.

Even then the result is a proposal. Nothing here calls `verify_decision`,
creates a run, or alters what the baseline produced.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, ValidationError

from app.ai.candidates import LinkProposalRequest
from app.ai.proposals import (
    LinkProposal,
    ProposalOutcome,
    ProviderIdentity,
    RawLinkSelection,
    bind,
)
from app.domain.evidence import EvidenceRef
from app.reconciliation.snapshot import FactSnapshot


class RejectionCode(StrEnum):
    """Why a proposal was refused. One code per way of being wrong.

    There is no code for answering about the wrong line or the wrong snapshot.
    A provider returns an outcome and a list of IDs, and the server writes the
    subject and the fingerprint from the request it holds, so those two failures
    are not checked for; they cannot be expressed. A response that tries to
    carry either is refused as `MALFORMED`, because a field the contract does
    not have is an extra.
    """

    MALFORMED = "MALFORMED"
    """It did not parse as a selection at all, or carried a field it may not."""

    OUT_OF_CANDIDATE_SET = "OUT_OF_CANDIDATE_SET"
    """It selected a record that was never offered."""

    PROVIDER_FAILED = "PROVIDER_FAILED"
    """The provider raised, timed out, or returned nothing."""


class ValidProposal(BaseModel):
    """A proposal that answers the right question from the right set.

    Still only a proposal. The name says selected records, not evidence and not
    a decision, because that is all it is until deterministic code turns it into
    references.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal: LinkProposal

    @property
    def selected(self) -> frozenset[str]:
        """Return the selection as a set, for comparison against the truth."""
        return frozenset(self.proposal.selected_source_record_ids)

    @property
    def abstained(self) -> bool:
        """Return whether the provider declined to select."""
        return self.proposal.outcome is ProposalOutcome.ABSTAIN


class RejectedProposal(BaseModel):
    """A proposal that was refused, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: RejectionCode
    detail: str
    """Written for a person reading a shadow report. Names the rule that was
    broken and the offending IDs, never the provider's own words: a provider
    that returned prose would otherwise have that prose stored and displayed."""


type ValidationResult = ValidProposal | RejectedProposal


def validate_selection(
    selection: RawLinkSelection,
    request: LinkProposalRequest,
    provider: ProviderIdentity,
) -> ValidationResult:
    """Check one parsed selection against the request, then bind it.

    Membership is the only thing left to check. The selection carries no subject
    and no fingerprint, so it cannot be about the wrong line or the wrong
    snapshot; the server writes both from the request when it binds.

    Args:
        selection: What the provider returned, already parsed.
        request: The exact question it was asked, carrying the candidate set.
        provider: The identity of the provider object that was called.

    Returns:
        A bound proposal, or a rejection naming what was wrong.
    """
    unknown = sorted(set(selection.selected_source_record_ids) - request.candidate_ids)
    if unknown:
        return RejectedProposal(
            code=RejectionCode.OUT_OF_CANDIDATE_SET,
            detail=f"the proposal selected records that were not offered: {unknown}",
        )

    return ValidProposal(
        proposal=bind(
            selection,
            subject_settlement_line_id=request.subject_settlement_line_id,
            snapshot_fingerprint=request.snapshot_fingerprint,
            provider=provider,
        )
    )


def parse_proposal(
    payload: object, request: LinkProposalRequest, provider: ProviderIdentity
) -> ValidationResult:
    """Parse whatever a provider returned, check it, and bind it.

    One call because a caller has no use for a selection that parsed and was
    never checked, and no use for one that was checked and never bound to the
    question it answers. Offering those as separate steps would make it possible
    to forget one.

    Args:
        payload: The raw provider output.
        request: The question it answers.
        provider: The identity of the provider object that was called. Read from
            the object, never from the payload, so a response cannot sign itself
            as somebody else.

    Returns:
        A bound proposal, or a rejection.
    """
    try:
        selection = RawLinkSelection.model_validate(payload)
    except ValidationError as error:
        return RejectedProposal(
            code=RejectionCode.MALFORMED,
            detail=f"the provider output is not a selection: {error.error_count()} problem(s)",
        )
    return validate_selection(selection, request, provider)


def evidence_for(valid: ValidProposal, snapshot: FactSnapshot) -> tuple[EvidenceRef, ...]:
    """Build evidence references for a validated selection.

    Deterministic code does this, not the provider. Each reference is built by
    loading the named fact and reading its own record ID, source system and
    payload hash, so a reference cannot describe a fact that is not there and
    cannot carry a hash the provider chose.

    Ordered by record ID, so the same selection always produces the same
    references whatever order the provider returned them in.

    Args:
        valid: A proposal that has already been checked against its request.
        snapshot: The facts the request was built from.

    Returns:
        One reference per selected record, in record ID order.
    """
    return tuple(
        EvidenceRef(
            source_record_id=record_id,
            source_system=snapshot.fact_for(record_id).source_system,
            payload_hash=snapshot.fact_for(record_id).payload_hash,
        )
        for record_id in sorted(valid.selected)
    )
