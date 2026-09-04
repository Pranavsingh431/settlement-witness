/**
 * Response fixtures, copied from real API responses.
 *
 * Taken from `docs/api.md` and from a running backend rather than invented, so
 * a test that passes here is a test against a shape the server actually sends.
 */

import type {
  BankFinalityAuditDetail,
  BankFinalityAuditPage,
  BankFinalityCertificate,
  DecisionView,
  DemoBatchResult,
  ImportReceipt,
  ReviewQueueItem,
  ReviewQueuePage,
  RunSummary,
  RunWorkboard,
} from '../api/types';

const NEW_RUN_GATE =
  'Import authoritative evidence and create a new reconciliation run. Close only when that new decision is RESOLVED, every citation verifies, and every required invariant holds.';

export const RUN: RunSummary = {
  run_id: 'fd0c9443bb7e4e5fb4eee88a79b6dc74',
  snapshot_fingerprint: '7092df18a31c4b93aa1f0d0e5f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c',
  baseline_version: '1.0.0',
  domain_schema_version: '5.0.0',
  parser_version: '3.1.0',
  created_at: '2026-08-26T11:41:19.621261Z',
  as_of: '2026-08-24T12:00:00Z',
  fact_count: 10,
  settlement_line_count: 3,
  decision_count: 3,
  status_counts: { RESOLVED: 1, EXCEPTION: 2, INSUFFICIENT_EVIDENCE: 0, PENDING: 0 },
  exception_counts: { PARTIAL_REFUND: 1, UNSUPPORTED_STATE: 1 },
};

export const RESOLVED_DECISION: DecisionView = {
  decision_id: '7092df18a31c4b93:line-0002',
  schema_version: '5.0.0',
  status: 'RESOLVED',
  subject_settlement_line_id: 'line-0002',
  linked_source_record_ids: ['2858d7ec:PSP_API:PAYMENT_EVENT:3'],
  linked_event_ids: ['pe-0002'],
  evidence: [
    {
      source_record_id: '2858d7ec:PSP_API:PAYMENT_EVENT:3',
      source_system: 'PSP_API',
      payload_hash: '81658ed3e4f1ad5ec76ab2ed72fde931507baf10c3e1aefee49943f02af9f55a',
      verification_outcome: 'VERIFIED',
    },
  ],
  invariant_results: [
    {
      invariant_id: 'INV-001',
      outcome: 'PASSED',
      reason_code: null,
      expected_minor: 244100,
      observed_minor: 244100,
    },
    {
      invariant_id: 'INV-004',
      outcome: 'NOT_APPLICABLE',
      reason_code: null,
      expected_minor: null,
      observed_minor: null,
    },
  ],
  exception_codes: [],
  reason_codes: ['ALL_REQUIRED_INVARIANTS_PASSED'],
  created_at: '2026-08-24T12:00:00Z',
  verified_evidence_count: 1,
  closure_plan: {
    plan_version: '1.0.0',
    baseline_status: 'RESOLVED',
    disposition: 'NO_ACTION',
    primary_owner: 'NONE',
    headline: 'No finance-ops follow-up is required for this decision.',
    blocking_codes: [],
    actions: [],
    requires_new_run: false,
    resolution_gate:
      'Already resolved by the recorded certificate. Later evidence creates a new run; it never edits this one.',
  },
};

export const EXCEPTION_DECISION: DecisionView = {
  decision_id: '7092df18a31c4b93:line-0001',
  schema_version: '5.0.0',
  status: 'EXCEPTION',
  subject_settlement_line_id: 'line-0001',
  linked_source_record_ids: ['2858d7ec:PSP_API:PAYMENT_EVENT:2'],
  linked_event_ids: ['pe-0001', 'pe-0003'],
  evidence: [
    {
      source_record_id: '2858d7ec:PSP_API:PAYMENT_EVENT:2',
      source_system: 'PSP_API',
      payload_hash: '2c1d4bb217febc17c0d4bfb33d869c27abb6fed7ee787943effa408b3d61d596',
      verification_outcome: 'VERIFIED',
    },
  ],
  invariant_results: [
    {
      invariant_id: 'INV-002',
      outcome: 'FAILED',
      reason_code: 'RETURNS_EXCEED_CAPTURE',
      expected_minor: 1000000,
      observed_minor: 1150000,
    },
  ],
  exception_codes: ['PARTIAL_REFUND'],
  reason_codes: ['EXCEPTION_CODE_REPORTED'],
  created_at: '2026-08-24T12:00:00Z',
  verified_evidence_count: 1,
  closure_plan: {
    plan_version: '1.0.0',
    baseline_status: 'EXCEPTION',
    disposition: 'ESCALATE',
    primary_owner: 'FINANCE_CONTROL',
    headline: 'Account for the residual balance',
    blocking_codes: ['PARTIAL_REFUND'],
    actions: [
      {
        action_code: 'PARTIAL_REFUND',
        owner_lane: 'FINANCE_CONTROL',
        title: 'Account for the residual balance',
        instruction: 'Explain the portion of the capture that remains after the recorded refund.',
        evidence_required:
          'A supported adjustment or terminal lifecycle record accounting for the remaining balance.',
        supported_by_current_contract: false,
      },
    ],
    requires_new_run: true,
    resolution_gate: NEW_RUN_GATE,
  },
};

export const INSUFFICIENT_DECISION: DecisionView = {
  decision_id: '7092df18a31c4b93:line-0003',
  schema_version: '5.0.0',
  status: 'INSUFFICIENT_EVIDENCE',
  subject_settlement_line_id: 'line-0003',
  linked_source_record_ids: ['missing-record'],
  linked_event_ids: [],
  evidence: [
    {
      source_record_id: 'missing-record',
      source_system: 'PSP_API',
      payload_hash: 'fa18b0a10d3dafe583cdd97022a7c6de651e311c173b21b1abf78d7b8bad2d87',
      verification_outcome: 'FACT_NOT_FOUND',
    },
  ],
  invariant_results: [
    {
      invariant_id: 'INV-003',
      outcome: 'INSUFFICIENT_INPUT',
      reason_code: 'EVIDENCE_FACT_NOT_FOUND',
      expected_minor: null,
      observed_minor: null,
    },
  ],
  exception_codes: ['INSUFFICIENT_EVIDENCE'],
  reason_codes: ['EVIDENCE_FACT_NOT_FOUND'],
  created_at: '2026-08-24T12:00:00Z',
  verified_evidence_count: 0,
  closure_plan: {
    plan_version: '1.0.0',
    baseline_status: 'INSUFFICIENT_EVIDENCE',
    disposition: 'COLLECT_EVIDENCE',
    primary_owner: 'EVIDENCE_OPERATIONS',
    headline: 'Request the missing evidence',
    blocking_codes: ['INSUFFICIENT_EVIDENCE'],
    actions: [
      {
        action_code: 'INSUFFICIENT_EVIDENCE',
        owner_lane: 'EVIDENCE_OPERATIONS',
        title: 'Request the missing evidence',
        instruction:
          'Read the certificate, identify each unverified citation or unrun invariant, and request it.',
        evidence_required: 'All cited facts present and every required invariant evaluable.',
        supported_by_current_contract: true,
      },
    ],
    requires_new_run: true,
    resolution_gate: NEW_RUN_GATE,
  },
};

/** Currency-separated triage copied from the run-workboard API contract. */
export const WORKBOARD: RunWorkboard = {
  run_id: RUN.run_id,
  snapshot_fingerprint: RUN.snapshot_fingerprint,
  workboard: {
    triage_version: '1.0.0',
    prioritisation_note:
      'Open work is ordered by absolute declared settlement net within each source currency. Currencies are never converted or summed. This is triage, not a cash-at-risk total.',
    currency_queues: [
      {
        currency: 'INR',
        items: [
          {
            decision_id: EXCEPTION_DECISION.decision_id,
            subject_settlement_line_id: EXCEPTION_DECISION.subject_settlement_line_id,
            status: 'EXCEPTION',
            exception_codes: ['PARTIAL_REFUND'],
            declared_settlement_value: {
              source_record_id: '2858d7ec:PSP_API:SETTLEMENT_LINE:1',
              payload_hash: '1fdd4bb217febc17c0d4bfb33d869c27abb6fed7ee787943effa408b3d61d596',
              net_minor: 1_000_000,
              currency: 'INR',
            },
            rank_in_currency: 1,
          },
          {
            decision_id: INSUFFICIENT_DECISION.decision_id,
            subject_settlement_line_id: INSUFFICIENT_DECISION.subject_settlement_line_id,
            status: 'INSUFFICIENT_EVIDENCE',
            exception_codes: ['INSUFFICIENT_EVIDENCE'],
            declared_settlement_value: {
              source_record_id: '2858d7ec:PSP_API:SETTLEMENT_LINE:3',
              payload_hash: '3fdd4bb217febc17c0d4bfb33d869c27abb6fed7ee787943effa408b3d61d596',
              net_minor: 488_200,
              currency: 'INR',
            },
            rank_in_currency: 2,
          },
        ],
      },
    ],
    unpriced_items: [],
  },
};

export const ACCEPTED_RECEIPT: ImportReceipt = {
  receipt_id: '5895047e27a746f2b978775f86d54553',
  document_hash: '2858d7ec1af5b652e3e9c7cac6c766a56023f6e97b08e0a9509305a8f8ec2618',
  document_name: 'payment_events.csv',
  source_system: 'PSP_API',
  source_record_type: 'PAYMENT_EVENT',
  parser_version: '3.1.0',
  received_at: '2026-08-26T11:42:23.299635Z',
  outcome: 'ACCEPTED',
  row_count: 2,
  accepted_count: 2,
  duplicate_count: 0,
  conflict_count: 0,
  rejected_count: 0,
  not_applied_count: 0,
  wrote_facts: true,
  failure_detail: null,
  row_outcomes: [
    {
      row_number: 2,
      outcome: 'ACCEPTED',
      source_record_id: '2858d7ec:PSP_API:PAYMENT_EVENT:2',
      code: null,
      detail: null,
    },
    {
      row_number: 3,
      outcome: 'ACCEPTED',
      source_record_id: '2858d7ec:PSP_API:PAYMENT_EVENT:3',
      code: null,
      detail: null,
    },
  ],
};

export const DUPLICATE_RECEIPT: ImportReceipt = {
  ...ACCEPTED_RECEIPT,
  receipt_id: '9b9cd9175fac41dab66a2eda6091ecfa',
  outcome: 'DUPLICATE_NO_OP',
  accepted_count: 0,
  duplicate_count: 2,
  wrote_facts: false,
  row_outcomes: ACCEPTED_RECEIPT.row_outcomes.map((row) => ({
    ...row,
    outcome: 'DUPLICATE_NO_OP' as const,
    code: 'DUPLICATE_NO_OP',
  })),
};

export const INVALID_RECEIPT: ImportReceipt = {
  receipt_id: 'baa15f638acf460497013b5df1ac862d',
  document_hash: '67234b98573d17cd27d28c56aadc31297294829bed613a4463cb3f20c31a2709',
  document_name: 'invalid_mixed_rows.csv',
  source_system: 'PSP_API',
  source_record_type: 'PAYMENT_EVENT',
  parser_version: '3.1.0',
  received_at: '2026-08-26T11:42:23.328795Z',
  outcome: 'REJECTED_INVALID',
  row_count: 3,
  accepted_count: 0,
  duplicate_count: 0,
  conflict_count: 0,
  rejected_count: 2,
  not_applied_count: 1,
  wrote_facts: false,
  failure_detail: '2 row(s) could not be read',
  row_outcomes: [
    {
      row_number: 2,
      outcome: 'NOT_APPLIED',
      source_record_id: '67234b98:PSP_API:PAYMENT_EVENT:2',
      code: null,
      detail: null,
    },
    {
      row_number: 3,
      outcome: 'REJECTED',
      source_record_id: null,
      code: 'INVALID_ENUM',
      detail:
        "event_type must be one of ['CAPTURE', 'CHARGEBACK', 'REFUND', 'REVERSAL'], got 'NOT_A_REAL_TYPE'",
    },
    {
      row_number: 4,
      outcome: 'REJECTED',
      source_record_id: null,
      code: 'MISSING_VALUE',
      detail: 'amount_minor is required and was empty',
    },
  ],
};

export const CONFLICT_RECEIPT: ImportReceipt = {
  ...INVALID_RECEIPT,
  receipt_id: 'cc51e4f5a2b34d1e9f0a7b6c5d4e3f21',
  document_name: 'conflicting_payment_events.csv',
  outcome: 'REJECTED_CONFLICT',
  row_count: 1,
  rejected_count: 0,
  conflict_count: 1,
  not_applied_count: 0,
  failure_detail: '1 row(s) contradict a stored fact',
  row_outcomes: [
    {
      row_number: 2,
      outcome: 'DUPLICATE_CONFLICT',
      source_record_id: '2690c9f0:PSP_API:PAYMENT_EVENT:2',
      code: 'DUPLICATE_CONFLICT',
      detail:
        'a fact with this identity is already stored with a different payload hash; stored 2c1d4bb2, incoming 81658ed3',
    },
  ],
};

/** The sentence the server sends with every review response. */
export const BASELINE_NOTE =
  'A review event records human workflow only. It does not change this ' +
  "decision's status, exception codes, invariant results or evidence, and " +
  'closing a review does not resolve the line.';

export const OPEN_ITEM: ReviewQueueItem = {
  run_id: RUN.run_id,
  decision: EXCEPTION_DECISION,
  decision_fingerprint: 'b31c1a2f4d0e5c6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a3b4c',
  workflow_state: 'OPEN',
  baseline_status: 'EXCEPTION',
  baseline_unchanged_note: BASELINE_NOTE,
  events: [],
};

export const UNKNOWN_ITEM: ReviewQueueItem = {
  run_id: RUN.run_id,
  decision: INSUFFICIENT_DECISION,
  decision_fingerprint: 'c42d2b3f5e1f6d7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a3b4c5d',
  workflow_state: 'WAITING_FOR_EVIDENCE',
  baseline_status: 'INSUFFICIENT_EVIDENCE',
  baseline_unchanged_note: BASELINE_NOTE,
  events: [
    {
      event_id: '2a4b6c8d0e2f4a6b8c0d2e4f6a8b0c2d',
      sequence: 1,
      action: 'REQUEST_EVIDENCE',
      note: 'need the 3 March bank statement',
      recorded_at: '2026-08-27T09:15:00Z',
      decision_fingerprint: 'c42d2b3f5e1f6d7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a3b4c5d',
    },
  ],
};

/** A closed item whose baseline status is still an exception. */
export const CLOSED_ITEM: ReviewQueueItem = {
  ...OPEN_ITEM,
  workflow_state: 'CLOSED_WITHOUT_OVERRIDE',
  events: [
    {
      event_id: '9f8e7d6c5b4a39281706f5e4d3c2b1a0',
      sequence: 1,
      action: 'CLOSED_WITHOUT_OVERRIDE',
      note: null,
      recorded_at: '2026-08-27T10:00:00Z',
      decision_fingerprint: OPEN_ITEM.decision_fingerprint,
    },
  ],
};

export const REVIEW_QUEUE: ReviewQueuePage = {
  run_id: RUN.run_id,
  review_contract_version: '1.0.0',
  items: [OPEN_ITEM, UNKNOWN_ITEM],
  total: 2,
  open_total: 2,
  limit: 20,
  offset: 0,
  baseline_unchanged_note: BASELINE_NOTE,
};

export const EMPTY_REVIEW_QUEUE: ReviewQueuePage = {
  ...REVIEW_QUEUE,
  items: [],
  total: 0,
  open_total: 0,
};

/** The sentence the server sends with every bank finality response. */
export const SEPARATE_NOTE =
  "A settlement decision says whether the provider's own records agree. Bank " +
  'finality says whether a bank statement shows the payout arriving. They are ' +
  'separate conclusions from separate evidence, and a line can be RESOLVED ' +
  'with no bank evidence at all.';

export const NO_BANK_AUDITS: BankFinalityAuditPage = {
  audits: [],
  total: 0,
  limit: 1,
  offset: 0,
  filtered: true,
  bank_finality_version: '1.0.0',
  settlement_and_finality_are_separate: SEPARATE_NOTE,
};

export const BANK_AUDIT: BankFinalityAuditPage['audits'][number] = {
  audit_id: 'a1f0c9443bb7e4e5fb4eee88a79b6dc7',
  snapshot_fingerprint: RUN.snapshot_fingerprint,
  bank_finality_version: '1.0.0',
  bank_statement_schema_version: '1.0.0',
  created_at: '2026-08-26T11:45:00Z',
  as_of: '2026-08-24T12:00:00Z',
  fact_count: 11,
  payout_count: 2,
  bank_transaction_count: 1,
  outcome_counts: {
    VERIFIED_BANK_CREDIT: 1,
    MISSING_BANK_EVIDENCE: 0,
    UNLINKABLE_PAYOUT: 1,
    AMBIGUOUS_BANK_EVIDENCE: 0,
    BANK_DIRECTION_MISMATCH: 0,
    BANK_AMOUNT_MISMATCH: 0,
    BANK_CURRENCY_MISMATCH: 0,
  },
  verified_payout_count: 1,
};

export const DEMO_BATCH: DemoBatchResult = {
  corpus_name: 'track-04-public-synthetic-batch',
  seed: 20260701,
  is_synthetic: true,
  scenario_count: 59,
  source_record_count: 180,
  decision_count: 59,
  source_documents: [
    { document_name: 'payment_events.csv', source_record_type: 'PAYMENT_EVENT', record_count: 65 },
    {
      document_name: 'settlement_lines.csv',
      source_record_type: 'SETTLEMENT_LINE',
      record_count: 59,
    },
    { document_name: 'payouts.csv', source_record_type: 'PAYOUT', record_count: 56 },
  ],
  resolved_count: 32,
  exception_count: 24,
  insufficient_evidence_count: 3,
  auto_match_rate: { numerator: 32, denominator: 59, value: 0.542373 },
  exception_breakdown: [
    {
      code: 'AMOUNT_MISMATCH',
      finding_count: 12,
      owner_lane: 'PSP_OPERATIONS',
      next_action:
        'Compare capture gross, settlement gross, deductions, and payout total at the source.',
      proof_required:
        'An authoritative correction or adjustment record that makes every amount invariant hold.',
      supported_by_current_contract: false,
    },
    {
      code: 'MISSING_PAYMENT',
      finding_count: 3,
      owner_lane: 'EVIDENCE_OPERATIONS',
      next_action: 'Request the provider payment-event export referenced by this settlement line.',
      proof_required: 'At least one valid PAYMENT_EVENT linked by the exact payment ID.',
      supported_by_current_contract: true,
    },
    {
      code: 'INSUFFICIENT_EVIDENCE',
      finding_count: 3,
      owner_lane: 'EVIDENCE_OPERATIONS',
      next_action:
        'Read the certificate, identify each unverified citation or unrun invariant, and request it.',
      proof_required: 'All cited facts present and every required invariant evaluable.',
      supported_by_current_contract: true,
    },
    {
      code: 'CURRENCY_MISMATCH',
      finding_count: 3,
      owner_lane: 'FINANCE_CONTROL',
      next_action: 'Confirm the source currency and whether an authorised conversion occurred.',
      proof_required: 'A supported FX or conversion record linking both currencies and amounts.',
      supported_by_current_contract: false,
    },
    {
      code: 'PARTIAL_REFUND',
      finding_count: 6,
      owner_lane: 'FINANCE_CONTROL',
      next_action: 'Explain the portion of the capture that remains after the recorded refund.',
      proof_required:
        'A supported adjustment or terminal lifecycle record accounting for the remaining balance.',
      supported_by_current_contract: false,
    },
    {
      code: 'OUT_OF_ORDER_EVENT',
      finding_count: 3,
      owner_lane: 'PSP_OPERATIONS',
      next_action: 'Check provider occurrence timestamps and the capture-to-return sequence.',
      proof_required:
        'Authoritative lifecycle evidence whose temporal order satisfies the contract.',
      supported_by_current_contract: false,
    },
    {
      code: 'UNSUPPORTED_STATE',
      finding_count: 3,
      owner_lane: 'FINANCE_CONTROL',
      next_action:
        'Record the case for policy review without forcing it into a nearby supported state.',
      proof_required:
        'A reviewed contract extension with fixtures and invariants for this lifecycle shape.',
      supported_by_current_contract: false,
    },
  ],
  processing_duration_ms: 412,
  throughput_lines_per_second: 143.2,
  contract_agreement: { numerator: 59, denominator: 59, value: 1 },
  exception_recall: { numerator: 33, denominator: 33, value: 1 },
  false_resolution_rate: { numerator: 0, denominator: 27, value: 0 },
  generator_version: '1.0.0',
  harness_version: '2.0.0',
  baseline_version: '1.0.0',
  limitation:
    'Generated regression corpus only. These numbers do not measure real-merchant performance, production accuracy or a production service level.',
};

export const BANK_AUDITS: BankFinalityAuditPage = {
  ...NO_BANK_AUDITS,
  audits: [BANK_AUDIT],
  total: 1,
};

export const VERIFIED_CERTIFICATE: BankFinalityCertificate = {
  payout_id: 'payout-0001',
  payout_source_record_id: '9c2f1a4b:PSP_API:PAYOUT:2',
  bank_reference: 'UTR2026082100001',
  outcome: 'VERIFIED_BANK_CREDIT',
  evidence: [
    {
      source_record_id: '9c2f1a4b:PSP_API:PAYOUT:2',
      source_system: 'PSP_API',
      payload_hash: 'aa18b0a10d3dafe583cdd97022a7c6de651e311c173b21b1abf78d7b8bad2d87',
      verification_outcome: 'VERIFIED',
    },
    {
      source_record_id: '3e7d5c1f:PSP_API:BANK_TRANSACTION:2',
      source_system: 'PSP_API',
      payload_hash: 'bb18b0a10d3dafe583cdd97022a7c6de651e311c173b21b1abf78d7b8bad2d87',
      verification_outcome: 'VERIFIED',
    },
  ],
  matched_bank_transaction_ids: ['BANKTXN0001'],
  expected_amount_minor: 1220500,
  expected_currency: 'INR',
  observed_amount_minor: 1220500,
  observed_currency: 'INR',
  observed_direction: 'CREDIT',
  recorded_at: '2026-08-24T12:00:00Z',
  schema_version: '1.0.0',
};

export const UNLINKABLE_CERTIFICATE: BankFinalityCertificate = {
  ...VERIFIED_CERTIFICATE,
  payout_id: 'payout-0002',
  payout_source_record_id: '9c2f1a4b:PSP_API:PAYOUT:3',
  bank_reference: null,
  outcome: 'UNLINKABLE_PAYOUT',
  evidence: [
    {
      source_record_id: '9c2f1a4b:PSP_API:PAYOUT:3',
      source_system: 'PSP_API',
      payload_hash: 'cc18b0a10d3dafe583cdd97022a7c6de651e311c173b21b1abf78d7b8bad2d87',
      verification_outcome: 'VERIFIED',
    },
  ],
  matched_bank_transaction_ids: [],
  expected_amount_minor: null,
  expected_currency: null,
  observed_amount_minor: null,
  observed_currency: null,
  observed_direction: null,
};

/** A one-minor-unit difference, which is a mismatch and not a rounding. */
export const AMOUNT_MISMATCH_CERTIFICATE: BankFinalityCertificate = {
  ...VERIFIED_CERTIFICATE,
  outcome: 'BANK_AMOUNT_MISMATCH',
  observed_amount_minor: 1220501,
};

export const BANK_AUDIT_DETAIL: BankFinalityAuditDetail = {
  audit: BANK_AUDIT,
  certificates: [VERIFIED_CERTIFICATE, UNLINKABLE_CERTIFICATE],
  filtered: false,
  settlement_and_finality_are_separate: SEPARATE_NOTE,
};
