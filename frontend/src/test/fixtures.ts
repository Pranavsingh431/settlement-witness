/**
 * Response fixtures, copied from real API responses.
 *
 * Taken from `docs/api.md` and from a running backend rather than invented, so
 * a test that passes here is a test against a shape the server actually sends.
 */

import type { DecisionView, ImportReceipt, RunSummary } from '../api/types';

export const RUN: RunSummary = {
  run_id: 'fd0c9443bb7e4e5fb4eee88a79b6dc74',
  snapshot_fingerprint: '7092df18a31c4b93aa1f0d0e5f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c',
  baseline_version: '1.0.0',
  domain_schema_version: '5.0.0',
  parser_version: '3.0.0',
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
};

export const ACCEPTED_RECEIPT: ImportReceipt = {
  receipt_id: '5895047e27a746f2b978775f86d54553',
  document_hash: '2858d7ec1af5b652e3e9c7cac6c766a56023f6e97b08e0a9509305a8f8ec2618',
  document_name: 'payment_events.csv',
  source_system: 'PSP_API',
  source_record_type: 'PAYMENT_EVENT',
  parser_version: '3.0.0',
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
  parser_version: '3.0.0',
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
