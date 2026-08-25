"""Building a payout as the snapshot sees it.

A payout document says what a batch totalled. It does not say which settlement
lines composed it, which is why `project_payout` leaves ``settlement_line_ids``
empty rather than inventing them.

INV-003 needs a payout that declares its contents, so one is built here from the
lines the snapshot actually has for that payout ID.

This is the boundary of what the check can mean, and it is worth stating
plainly. INV-003 passing says the payout total equals the sum of the lines this
system holds. It does not say the provider's export was complete, and it cannot:
a line that was never imported leaves no trace to notice. That is why a missing
payout produces `INSUFFICIENT_EVIDENCE` rather than a resolution, and why the
documentation describes every payout grouping as snapshot relative.
"""

from collections.abc import Sequence

from app.domain.lifecycle import PayoutBatch, SettlementLine


def snapshot_payout(payout: PayoutBatch, evidenced: Sequence[SettlementLine]) -> PayoutBatch:
    """Return the payout with the lines this snapshot has for it.

    Args:
        payout: The payout as projected from its own document.
        evidenced: The settlement lines in the snapshot naming this payout.

    Returns:
        A copy declaring those lines, so INV-003 has something to check against.
    """
    return payout.model_copy(
        update={"settlement_line_ids": tuple(sorted(line.settlement_line_id for line in evidenced))}
    )
