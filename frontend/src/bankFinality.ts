/**
 * The words this application uses for bank finality outcomes.
 *
 * Indexed by string rather than by the union type, for the same reason every
 * other vocabulary map here is: the backend owns these values, and one this
 * build has not heard of must render as something a person can read rather than
 * as a blank.
 *
 * None of these labels is the word "resolved", and none of them is a tick on
 * its own. A settlement decision and a bank finality outcome are different
 * conclusions from different evidence, and this file must never make them look
 * alike.
 */

export interface FinalityLabel {
  /** Which of the three visual treatments this outcome gets. */
  readonly tone: 'verified' | 'absent' | 'discrepancy';
  readonly label: string;
  /** What the records actually said, in a sentence. */
  readonly what: string;
}

export const FINALITY_LABELS: Record<string, FinalityLabel> = {
  VERIFIED_BANK_CREDIT: {
    tone: 'verified',
    label: 'Bank credit verified',
    what: 'Exactly one credit carrying this payout reference, for this exact amount and currency.',
  },
  MISSING_BANK_EVIDENCE: {
    tone: 'absent',
    label: 'No bank evidence',
    what: 'The payout names a reference and no imported statement row carries it. That is not a claim the money failed to arrive; it is a statement that this system has not been shown it arriving.',
  },
  UNLINKABLE_PAYOUT: {
    tone: 'absent',
    label: 'No reference to match on',
    what: 'The payout carries no bank reference, so no exact association is possible. This is a gap in the provider record, not a discrepancy, and it is reported rather than guessed around.',
  },
  AMBIGUOUS_BANK_EVIDENCE: {
    tone: 'discrepancy',
    label: 'More than one candidate',
    what: 'Two or more statement rows carry this reference. Choosing one would be inventing a fact about which transfer this was, so every candidate is listed.',
  },
  BANK_DIRECTION_MISMATCH: {
    tone: 'discrepancy',
    label: 'Wrong direction',
    what: 'The one row carrying this reference is a debit. Money leaving the account is not weaker evidence that the payout arrived.',
  },
  BANK_AMOUNT_MISMATCH: {
    tone: 'discrepancy',
    label: 'Amount differs',
    what: 'The credit is for a different number of minor units. There is no tolerance band: one minor unit is a difference.',
  },
  BANK_CURRENCY_MISMATCH: {
    tone: 'discrepancy',
    label: 'Currency differs',
    what: 'The credit is in a different currency, so the amounts cannot be compared at all.',
  },
};

/** The marks each tone uses. Never a plain tick shared with a settled line. */
export const FINALITY_MARKS: Record<string, string> = {
  verified: '⌁',
  absent: '·',
  discrepancy: '!',
};
