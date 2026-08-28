"""What a model is allowed to say, and what it is allowed to say it about.

The whole of this module is a boundary. A model in this system may point at
records the application already showed it. It may not assert anything about
them, and there is deliberately no field here through which it could.

`DecisionCandidate` is not reused for this. That type carries exception codes,
invariant results and evidence references with payload hashes, and every one of
those is something the verifier derives or that deterministic code constructs
from real facts. Letting model output arrive in that shape would put a
generated value one `model_validate` away from a stored conclusion. A separate,
narrower type means the unsafe fields do not exist to be filled in.

What is absent is the design:

- no status, so nothing here can be read as a conclusion;
- no exception code, so a model cannot report a finding;
- no reason code, so it cannot explain a status it did not derive;
- no payload hash, so it cannot say which version of a fact it saw;
- no invariant result, so it cannot claim a check passed;
- no confidence, so nothing invites weighing a guess against a check;
- no free text, so there is no place for a justification to be stored, shown,
  or later mistaken for evidence;
- no money amount and no lifecycle claim, so it cannot restate the records.

`extra="forbid"` on every model here means a provider that returns any of those
is rejected rather than silently trimmed.

## Two layers, because metadata is not the provider's to give

A provider returns a `RawLinkSelection`: an outcome and a list of record IDs.
That is the whole of what it may say.

Everything else on a proposal is written by the server from what it already
knows. Which line was asked about, which snapshot the question was against, and
which provider answered are all facts the caller holds before it calls anything,
and taking them from the response instead would mean a provider could name a
different line, claim a different snapshot, or sign the answer as somebody else.
An audit trail assembled partly from the thing being audited is not one.

`bind` is where that happens, and it is the normal-path construction route for a
`LinkProposal`. Direct construction stays possible in Python, as it does for any
Pydantic model, so the envelope repeats the shape checks as a defensive boundary
and a test builds one directly to exercise them.
"""

import hashlib
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SELECTED_RECORDS = 64
"""The most records one proposal may select.

A bound, not a policy about linking. Without it a provider could return a
selection large enough to be expensive to validate, and the request that
produced it named a finite candidate set anyway, so nothing legitimate needs
more than that set holds."""


class ProposalOutcome(StrEnum):
    """The two things a provider may answer. There is no third."""

    PROPOSE = "PROPOSE"
    """The provider selected records from the candidate set it was given."""

    ABSTAIN = "ABSTAIN"
    """The provider declined to select.

    A first-class answer rather than a failure. A provider that cannot tell
    which records belong to a line should say so, and an abstention that is
    reported honestly is worth more than a guess that has to be caught later."""


class RawLinkSelection(BaseModel):
    """Everything a provider is allowed to return.

    Two fields. Not the subject line, not the snapshot, not its own identity and
    not a proposal ID: those are the caller's knowledge, not the provider's, and
    a field here for any of them would be a field a provider could get wrong or
    lie about.

    `extra="forbid"` therefore refuses more than the obviously dangerous keys. A
    response carrying `provider`, `snapshot_fingerprint`, `page_ordinal`,
    `environment_fingerprint` or `request_fingerprint` is refused too, even
    though a correct value for each exists, because a provider supplying one
    has misunderstood what it is being asked and the rest of its answer is not
    worth salvaging.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ProposalOutcome
    selected_source_record_ids: tuple[str, ...] = ()
    """Ordered, because the order a provider returned is part of what it said."""

    @model_validator(mode="after")
    def _outcome_matches_selection(self) -> Self:
        """Refuse a selection whose outcome and contents disagree.

        An abstention carrying records and a proposal carrying none are both
        contradictions, and either would leave a reader to guess which half was
        meant. A duplicate is refused because a set of records has no room for
        one twice, and accepting it would make the selection's length mean two
        different things.
        """
        selected = self.selected_source_record_ids
        if self.outcome is ProposalOutcome.ABSTAIN and selected:
            message = f"an abstention selected {len(selected)} record(s); it must select none"
            raise ValueError(message)
        if self.outcome is ProposalOutcome.PROPOSE:
            if not selected:
                message = "a proposal selected no records; it must select at least one"
                raise ValueError(message)
            if len(set(selected)) != len(selected):
                repeated = sorted({one for one in selected if selected.count(one) > 1})
                message = f"a proposal selected the same record more than once: {repeated}"
                raise ValueError(message)
            if len(selected) > MAX_SELECTED_RECORDS:
                message = (
                    f"a proposal selected {len(selected)} records, "
                    f"more than the {MAX_SELECTED_RECORDS} allowed"
                )
                raise ValueError(message)
        return self


class ProviderIdentity(BaseModel):
    """Which provider produced a proposal, for the audit trail.

    Enough to tell two providers apart and to tell two versions of one provider
    apart, because a proposal is only interpretable against the thing that made
    it. Deliberately not a place for a request ID, a latency, a token count or
    anything else that would grow into telemetry attached to a claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)


class LinkProposal(BaseModel):
    """One provider's answer, bound to the question that produced it.

    Assembled by `bind` from a raw selection, the request it answers and the
    provider object that was called. Every field except the selection is the
    server's own knowledge, so a proposal cannot name the wrong line, claim the
    wrong snapshot, or carry an identity the provider chose for itself.

    A valid proposal is a proposal. It does not become evidence, does not reach
    `verify_decision`, and does not create a run. Deterministic code turns a
    validated selection into evidence references by loading the facts itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=128)
    subject_settlement_line_id: str = Field(min_length=1, max_length=200)
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    environment_fingerprint: str = Field(min_length=64, max_length=64)
    """Which candidate universe this page was cut from."""

    page_ordinal: int = Field(ge=1)
    """Which page of that universe was asked. Part of the identity: without it,
    two pages of one line in one snapshot would be the same record."""

    request_fingerprint: str = Field(min_length=64, max_length=64)
    """A digest of everything the page showed the provider.

    The environment fingerprint says which records the universe held. This says
    what was shown about them, which is a different question and a different
    task: the same records rendered canonically, truncated and withheld are
    three questions, and under the environment fingerprint alone all three were
    one record."""

    outcome: ProposalOutcome
    selected_source_record_ids: tuple[str, ...] = ()
    """Ordered, because the order a provider returned is part of what it said.

    Kept as given rather than sorted, so a validator can report a duplicate at
    the position it appeared and an evaluator compares sets explicitly rather
    than by accident."""

    provider: ProviderIdentity

    @model_validator(mode="after")
    def _outcome_matches_selection(self) -> Self:
        """Refuse an envelope whose outcome and selection disagree.

        The same rules `RawLinkSelection` enforces, applied again on the way
        out. `bind` only ever builds this from a selection that has already
        passed them, so nothing in the normal path can reach these branches.
        They are kept because this type is the one that would be handed onward,
        and a check that costs nothing on a boundary is worth having twice.
        """
        selected = self.selected_source_record_ids
        if self.outcome is ProposalOutcome.ABSTAIN and selected:
            message = f"an abstention selected {len(selected)} record(s); it must select none"
            raise ValueError(message)
        if self.outcome is ProposalOutcome.PROPOSE:
            if not selected:
                message = "a proposal selected no records; it must select at least one"
                raise ValueError(message)
            if len(set(selected)) != len(selected):
                repeated = sorted({one for one in selected if selected.count(one) > 1})
                message = f"a proposal selected the same record more than once: {repeated}"
                raise ValueError(message)
            if len(selected) > MAX_SELECTED_RECORDS:
                message = (
                    f"a proposal selected {len(selected)} records, "
                    f"more than the {MAX_SELECTED_RECORDS} allowed"
                )
                raise ValueError(message)
        return self


def proposal_id_for(
    *,
    snapshot_fingerprint: str,
    subject_settlement_line_id: str,
    environment_fingerprint: str,
    page_ordinal: int,
    request_fingerprint: str,
    provider: ProviderIdentity,
) -> str:
    """Return the identity of one proposal attempt.

    Derived rather than random, so that the same provider asked the same
    question about the same snapshot produces a byte-identical record. That is
    what makes a shadow evaluation reproducible, and it means a proposal cannot
    be told apart from a replay of itself by its identity alone.

    The page and the environment are part of the question, so they are part of
    the identity. Without them, every page of one line would be filed under the
    same ID and a report would carry several records claiming to be one.

    So is what the page showed. The same provider, line, snapshot and page under
    two different renderings answered two different questions, and filing both
    under one ID would make a report over one presentation look like a replay of
    a report over another.
    """
    digest = hashlib.sha256()
    for part in (
        snapshot_fingerprint,
        subject_settlement_line_id,
        environment_fingerprint,
        str(page_ordinal),
        request_fingerprint,
        provider.name,
        provider.version,
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


def bind(
    selection: RawLinkSelection,
    *,
    subject_settlement_line_id: str,
    snapshot_fingerprint: str,
    environment_fingerprint: str,
    page_ordinal: int,
    request_fingerprint: str,
    provider: ProviderIdentity,
) -> LinkProposal:
    """Attach a raw selection to the question and the provider it came from.

    The normal-path construction route for a `LinkProposal`, and the only one
    any caller should use. Direct construction remains possible in Python, as it
    does for every Pydantic model, and the envelope keeps its own shape checks
    for exactly that reason; a test exercises them by building one directly.

    Every field but the selection comes from the caller's own knowledge: the
    line it asked about, the snapshot and environment it asked against, which
    page, and the provider object it called. The proposal ID is derived here
    rather than accepted, so two records of the same question by the same
    provider are the same record and a provider cannot choose what its answer is
    filed under.

    Args:
        selection: What the provider returned, already parsed.
        subject_settlement_line_id: The line the request was about.
        snapshot_fingerprint: The snapshot the request was against.
        environment_fingerprint: The candidate universe the page was cut from.
        page_ordinal: Which page of that universe was asked.
        request_fingerprint: A digest of what the page showed the provider.
        provider: The identity read from the provider object that was called,
            never from its response.

    Returns:
        The bound proposal.
    """
    return LinkProposal(
        proposal_id=proposal_id_for(
            snapshot_fingerprint=snapshot_fingerprint,
            subject_settlement_line_id=subject_settlement_line_id,
            environment_fingerprint=environment_fingerprint,
            page_ordinal=page_ordinal,
            request_fingerprint=request_fingerprint,
            provider=provider,
        ),
        subject_settlement_line_id=subject_settlement_line_id,
        snapshot_fingerprint=snapshot_fingerprint,
        environment_fingerprint=environment_fingerprint,
        page_ordinal=page_ordinal,
        request_fingerprint=request_fingerprint,
        outcome=selection.outcome,
        selected_source_record_ids=selection.selected_source_record_ids,
        provider=provider,
    )
