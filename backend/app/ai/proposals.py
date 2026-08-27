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
    """One provider's answer for one settlement line, in one snapshot.

    Carries an ordered selection of source record IDs and nothing else. The IDs
    are checked against the candidate set by `app.ai.validation`; this type
    enforces only the shape, because a well-formed proposal that names a record
    from another line is still well-formed.

    A valid proposal is a proposal. It does not become evidence, does not reach
    `verify_decision`, and does not create a run. Deterministic code turns a
    validated selection into evidence references by loading the facts itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=128)
    subject_settlement_line_id: str = Field(min_length=1, max_length=200)
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    outcome: ProposalOutcome
    selected_source_record_ids: tuple[str, ...] = ()
    """Ordered, because the order a provider returned is part of what it said.

    Kept as given rather than sorted, so a validator can report a duplicate at
    the position it appeared and an evaluator compares sets explicitly rather
    than by accident."""

    provider: ProviderIdentity

    @model_validator(mode="after")
    def _outcome_matches_selection(self) -> Self:
        """Refuse a proposal whose outcome and selection disagree.

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


def proposal_id_for(
    *, snapshot_fingerprint: str, subject_settlement_line_id: str, provider: ProviderIdentity
) -> str:
    """Return the identity of one proposal attempt.

    Derived rather than random, so that the same provider asked the same
    question about the same snapshot produces a byte-identical record. That is
    what makes a shadow evaluation reproducible, and it means a proposal cannot
    be told apart from a replay of itself by its identity alone.
    """
    digest = hashlib.sha256()
    for part in (snapshot_fingerprint, subject_settlement_line_id, provider.name, provider.version):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]
