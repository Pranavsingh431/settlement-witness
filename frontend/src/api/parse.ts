/**
 * Checking that a response is the shape it claims to be.
 *
 * A cast would let a missing field reach the interface as `undefined` and be
 * rendered as an empty cell, which looks like a fact about the data rather than
 * a defect in the plumbing. These read every field the interface depends on and
 * refuse anything else, so a mismatch between this app and the API is an error
 * on screen instead of a blank.
 *
 * Enum values are deliberately not checked against a fixed list. The backend
 * owns those vocabularies and can add to them, and refusing a whole run because
 * one decision carries a code this build has not heard of would be worse than
 * showing the code as it arrived.
 */

import { MalformedResponseError } from './errors';
import type {
  BankDirection,
  BankFinalityAuditDetail,
  BankFinalityAuditPage,
  BankFinalityAuditSummary,
  BankFinalityCertificate,
  BankFinalityOutcome,
  DemoBatchResult,
  DemoDocumentSummary,
  DemoExceptionSummary,
  DecisionStatus,
  DecisionView,
  EvidenceReference,
  ImportReceipt,
  ImportReceiptPage,
  InvariantCheck,
  MeasuredRate,
  ReviewAction,
  ReviewEventReceipt,
  ReviewEventView,
  ReviewQueueItem,
  ReviewQueuePage,
  ReviewWorkflowState,
  RowOutcomeView,
  RunDetail,
  RunPage,
  RunSummary,
} from './types';

type Raw = Record<string, unknown>;

function object(value: unknown, what: string): Raw {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new MalformedResponseError(`${what} is not an object`);
  }
  return value as Raw;
}

function str(source: Raw, key: string, what: string): string {
  const value = source[key];
  if (typeof value !== 'string') {
    throw new MalformedResponseError(`${what} is missing the text field "${key}"`);
  }
  return value;
}

function num(source: Raw, key: string, what: string): number {
  const value = source[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new MalformedResponseError(`${what} is missing the number field "${key}"`);
  }
  return value;
}

function bool(source: Raw, key: string, what: string): boolean {
  const value = source[key];
  if (typeof value !== 'boolean') {
    throw new MalformedResponseError(`${what} is missing the true or false field "${key}"`);
  }
  return value;
}

function nullableStr(source: Raw, key: string, what: string): string | null {
  const value = source[key];
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== 'string') {
    throw new MalformedResponseError(`${what} has a non-text value in "${key}"`);
  }
  return value;
}

function nullableNum(source: Raw, key: string, what: string): number | null {
  const value = source[key];
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new MalformedResponseError(`${what} has a non-numeric value in "${key}"`);
  }
  return value;
}

function list<T>(source: Raw, key: string, what: string, each: (item: unknown) => T): T[] {
  const value = source[key];
  if (!Array.isArray(value)) {
    throw new MalformedResponseError(`${what} is missing the list "${key}"`);
  }
  return value.map(each);
}

function strings(source: Raw, key: string, what: string): string[] {
  return list(source, key, what, (item) => {
    if (typeof item !== 'string') {
      throw new MalformedResponseError(`${what} has a non-text entry in "${key}"`);
    }
    return item;
  });
}

function counts(source: Raw, key: string, what: string): Record<string, number> {
  const value = object(source[key], `${what}.${key}`);
  const result: Record<string, number> = {};
  for (const [name, count] of Object.entries(value)) {
    if (typeof count !== 'number' || !Number.isFinite(count)) {
      throw new MalformedResponseError(`${what}.${key} has a non-numeric count for "${name}"`);
    }
    result[name] = count;
  }
  return result;
}

export function parseRunSummary(value: unknown): RunSummary {
  const raw = object(value, 'run');
  return {
    run_id: str(raw, 'run_id', 'run'),
    snapshot_fingerprint: str(raw, 'snapshot_fingerprint', 'run'),
    baseline_version: str(raw, 'baseline_version', 'run'),
    domain_schema_version: str(raw, 'domain_schema_version', 'run'),
    parser_version: str(raw, 'parser_version', 'run'),
    created_at: str(raw, 'created_at', 'run'),
    as_of: str(raw, 'as_of', 'run'),
    fact_count: num(raw, 'fact_count', 'run'),
    settlement_line_count: num(raw, 'settlement_line_count', 'run'),
    decision_count: num(raw, 'decision_count', 'run'),
    status_counts: counts(raw, 'status_counts', 'run'),
    exception_counts: counts(raw, 'exception_counts', 'run'),
  };
}

function parseRate(value: unknown, what: string): MeasuredRate {
  const raw = object(value, what);
  return {
    numerator: num(raw, 'numerator', what),
    denominator: num(raw, 'denominator', what),
    value: nullableNum(raw, 'value', what),
  };
}

function parseDemoDocument(value: unknown): DemoDocumentSummary {
  const raw = object(value, 'demo document');
  return {
    document_name: str(raw, 'document_name', 'demo document'),
    source_record_type: str(
      raw,
      'source_record_type',
      'demo document',
    ) as DemoDocumentSummary['source_record_type'],
    record_count: num(raw, 'record_count', 'demo document'),
  };
}

function parseDemoException(value: unknown): DemoExceptionSummary {
  const raw = object(value, 'demo exception');
  return {
    code: str(raw, 'code', 'demo exception'),
    finding_count: num(raw, 'finding_count', 'demo exception'),
  };
}

function parseEvidence(value: unknown): EvidenceReference {
  const raw = object(value, 'evidence reference');
  return {
    source_record_id: str(raw, 'source_record_id', 'evidence reference'),
    source_system: str(raw, 'source_system', 'evidence reference'),
    payload_hash: str(raw, 'payload_hash', 'evidence reference'),
    verification_outcome: nullableStr(
      raw,
      'verification_outcome',
      'evidence reference',
    ) as EvidenceReference['verification_outcome'],
  };
}

function parseInvariant(value: unknown): InvariantCheck {
  const raw = object(value, 'invariant result');
  return {
    invariant_id: str(raw, 'invariant_id', 'invariant result'),
    outcome: str(raw, 'outcome', 'invariant result') as InvariantCheck['outcome'],
    reason_code: nullableStr(raw, 'reason_code', 'invariant result'),
    expected_minor: nullableNum(raw, 'expected_minor', 'invariant result'),
    observed_minor: nullableNum(raw, 'observed_minor', 'invariant result'),
  };
}

export function parseDecision(value: unknown): DecisionView {
  const raw = object(value, 'decision');
  return {
    decision_id: str(raw, 'decision_id', 'decision'),
    schema_version: str(raw, 'schema_version', 'decision'),
    status: str(raw, 'status', 'decision') as DecisionView['status'],
    subject_settlement_line_id: str(raw, 'subject_settlement_line_id', 'decision'),
    linked_source_record_ids: strings(raw, 'linked_source_record_ids', 'decision'),
    linked_event_ids: strings(raw, 'linked_event_ids', 'decision'),
    evidence: list(raw, 'evidence', 'decision', parseEvidence),
    invariant_results: list(raw, 'invariant_results', 'decision', parseInvariant),
    exception_codes: strings(raw, 'exception_codes', 'decision'),
    reason_codes: strings(raw, 'reason_codes', 'decision'),
    created_at: str(raw, 'created_at', 'decision'),
    verified_evidence_count: num(raw, 'verified_evidence_count', 'decision'),
  };
}

export function parseRunPage(value: unknown): RunPage {
  const raw = object(value, 'run page');
  return {
    runs: list(raw, 'runs', 'run page', parseRunSummary),
    total: num(raw, 'total', 'run page'),
    limit: num(raw, 'limit', 'run page'),
    offset: num(raw, 'offset', 'run page'),
  };
}

export function parseRunDetail(value: unknown): RunDetail {
  const raw = object(value, 'run detail');
  return {
    run: parseRunSummary(raw.run),
    decisions: list(raw, 'decisions', 'run detail', parseDecision),
    filtered: bool(raw, 'filtered', 'run detail'),
  };
}

function parseRowOutcome(value: unknown): RowOutcomeView {
  const raw = object(value, 'row outcome');
  return {
    row_number: num(raw, 'row_number', 'row outcome'),
    outcome: str(raw, 'outcome', 'row outcome') as RowOutcomeView['outcome'],
    source_record_id: nullableStr(raw, 'source_record_id', 'row outcome'),
    code: nullableStr(raw, 'code', 'row outcome'),
    detail: nullableStr(raw, 'detail', 'row outcome'),
  };
}

export function parseReceipt(value: unknown): ImportReceipt {
  const raw = object(value, 'receipt');
  return {
    receipt_id: str(raw, 'receipt_id', 'receipt'),
    document_hash: str(raw, 'document_hash', 'receipt'),
    document_name: str(raw, 'document_name', 'receipt'),
    source_system: str(raw, 'source_system', 'receipt'),
    source_record_type: str(raw, 'source_record_type', 'receipt'),
    parser_version: str(raw, 'parser_version', 'receipt'),
    received_at: str(raw, 'received_at', 'receipt'),
    outcome: str(raw, 'outcome', 'receipt') as ImportReceipt['outcome'],
    row_count: num(raw, 'row_count', 'receipt'),
    accepted_count: num(raw, 'accepted_count', 'receipt'),
    duplicate_count: num(raw, 'duplicate_count', 'receipt'),
    conflict_count: num(raw, 'conflict_count', 'receipt'),
    rejected_count: num(raw, 'rejected_count', 'receipt'),
    not_applied_count: num(raw, 'not_applied_count', 'receipt'),
    wrote_facts: bool(raw, 'wrote_facts', 'receipt'),
    failure_detail: nullableStr(raw, 'failure_detail', 'receipt'),
    row_outcomes: list(raw, 'row_outcomes', 'receipt', parseRowOutcome),
  };
}

export function parseReceiptPage(value: unknown): ImportReceiptPage {
  const raw = object(value, 'receipt page');
  return {
    receipts: list(raw, 'receipts', 'receipt page', parseReceipt),
    total: num(raw, 'total', 'receipt page'),
    limit: num(raw, 'limit', 'receipt page'),
    offset: num(raw, 'offset', 'receipt page'),
    filtered: bool(raw, 'filtered', 'receipt page'),
  };
}

function parseReviewEvent(value: unknown): ReviewEventView {
  const raw = object(value, 'review event');
  return {
    event_id: str(raw, 'event_id', 'review event'),
    sequence: num(raw, 'sequence', 'review event'),
    action: str(raw, 'action', 'review event') as ReviewAction,
    note: nullableStr(raw, 'note', 'review event'),
    recorded_at: str(raw, 'recorded_at', 'review event'),
    decision_fingerprint: str(raw, 'decision_fingerprint', 'review event'),
  };
}

export function parseReviewQueueItem(value: unknown): ReviewQueueItem {
  const raw = object(value, 'review queue item');
  return {
    run_id: str(raw, 'run_id', 'review queue item'),
    decision: parseDecision(raw.decision),
    decision_fingerprint: str(raw, 'decision_fingerprint', 'review queue item'),
    workflow_state: str(raw, 'workflow_state', 'review queue item') as ReviewWorkflowState,
    baseline_status: str(raw, 'baseline_status', 'review queue item') as DecisionStatus,
    baseline_unchanged_note: str(raw, 'baseline_unchanged_note', 'review queue item'),
    events: list(raw, 'events', 'review queue item', parseReviewEvent),
  };
}

export function parseReviewQueuePage(value: unknown): ReviewQueuePage {
  const raw = object(value, 'review queue');
  return {
    run_id: str(raw, 'run_id', 'review queue'),
    review_contract_version: str(raw, 'review_contract_version', 'review queue'),
    items: list(raw, 'items', 'review queue', parseReviewQueueItem),
    total: num(raw, 'total', 'review queue'),
    open_total: num(raw, 'open_total', 'review queue'),
    limit: num(raw, 'limit', 'review queue'),
    offset: num(raw, 'offset', 'review queue'),
    baseline_unchanged_note: str(raw, 'baseline_unchanged_note', 'review queue'),
  };
}

export function parseReviewEventReceipt(value: unknown): ReviewEventReceipt {
  const raw = object(value, 'review event receipt');
  return {
    event: parseReviewEvent(raw.event),
    workflow_state: str(raw, 'workflow_state', 'review event receipt') as ReviewWorkflowState,
    baseline_status: str(raw, 'baseline_status', 'review event receipt') as DecisionStatus,
    baseline_unchanged_note: str(raw, 'baseline_unchanged_note', 'review event receipt'),
  };
}

export function parseBankFinalityAuditSummary(value: unknown): BankFinalityAuditSummary {
  const raw = object(value, 'bank finality audit');
  return {
    audit_id: str(raw, 'audit_id', 'bank finality audit'),
    snapshot_fingerprint: str(raw, 'snapshot_fingerprint', 'bank finality audit'),
    bank_finality_version: str(raw, 'bank_finality_version', 'bank finality audit'),
    bank_statement_schema_version: str(raw, 'bank_statement_schema_version', 'bank finality audit'),
    created_at: str(raw, 'created_at', 'bank finality audit'),
    as_of: str(raw, 'as_of', 'bank finality audit'),
    fact_count: num(raw, 'fact_count', 'bank finality audit'),
    payout_count: num(raw, 'payout_count', 'bank finality audit'),
    bank_transaction_count: num(raw, 'bank_transaction_count', 'bank finality audit'),
    outcome_counts: counts(raw, 'outcome_counts', 'bank finality audit'),
    verified_payout_count: num(raw, 'verified_payout_count', 'bank finality audit'),
  };
}

export function parseBankFinalityCertificate(value: unknown): BankFinalityCertificate {
  const raw = object(value, 'bank finality certificate');
  return {
    payout_id: str(raw, 'payout_id', 'bank finality certificate'),
    payout_source_record_id: str(raw, 'payout_source_record_id', 'bank finality certificate'),
    bank_reference: nullableStr(raw, 'bank_reference', 'bank finality certificate'),
    outcome: str(raw, 'outcome', 'bank finality certificate') as BankFinalityOutcome,
    evidence: list(raw, 'evidence', 'bank finality certificate', parseEvidence),
    matched_bank_transaction_ids: strings(
      raw,
      'matched_bank_transaction_ids',
      'bank finality certificate',
    ),
    expected_amount_minor: nullableNum(raw, 'expected_amount_minor', 'bank finality certificate'),
    expected_currency: nullableStr(raw, 'expected_currency', 'bank finality certificate'),
    observed_amount_minor: nullableNum(raw, 'observed_amount_minor', 'bank finality certificate'),
    observed_currency: nullableStr(raw, 'observed_currency', 'bank finality certificate'),
    observed_direction: nullableStr(
      raw,
      'observed_direction',
      'bank finality certificate',
    ) as BankDirection | null,
    recorded_at: str(raw, 'recorded_at', 'bank finality certificate'),
    schema_version: str(raw, 'schema_version', 'bank finality certificate'),
  };
}

export function parseBankFinalityAuditDetail(value: unknown): BankFinalityAuditDetail {
  const raw = object(value, 'bank finality audit');
  return {
    audit: parseBankFinalityAuditSummary(raw.audit),
    certificates: list(raw, 'certificates', 'bank finality audit', parseBankFinalityCertificate),
    filtered: bool(raw, 'filtered', 'bank finality audit'),
    settlement_and_finality_are_separate: str(
      raw,
      'settlement_and_finality_are_separate',
      'bank finality audit',
    ),
  };
}

export function parseBankFinalityAuditPage(value: unknown): BankFinalityAuditPage {
  const raw = object(value, 'bank finality audits');
  return {
    audits: list(raw, 'audits', 'bank finality audits', parseBankFinalityAuditSummary),
    total: num(raw, 'total', 'bank finality audits'),
    limit: num(raw, 'limit', 'bank finality audits'),
    offset: num(raw, 'offset', 'bank finality audits'),
    filtered: bool(raw, 'filtered', 'bank finality audits'),
    bank_finality_version: str(raw, 'bank_finality_version', 'bank finality audits'),
    settlement_and_finality_are_separate: str(
      raw,
      'settlement_and_finality_are_separate',
      'bank finality audits',
    ),
  };
}

export function parseDemoBatch(value: unknown): DemoBatchResult {
  const raw = object(value, 'demo batch');
  return {
    corpus_name: str(raw, 'corpus_name', 'demo batch'),
    seed: num(raw, 'seed', 'demo batch'),
    is_synthetic: bool(raw, 'is_synthetic', 'demo batch'),
    scenario_count: num(raw, 'scenario_count', 'demo batch'),
    source_record_count: num(raw, 'source_record_count', 'demo batch'),
    decision_count: num(raw, 'decision_count', 'demo batch'),
    source_documents: list(raw, 'source_documents', 'demo batch', parseDemoDocument),
    resolved_count: num(raw, 'resolved_count', 'demo batch'),
    exception_count: num(raw, 'exception_count', 'demo batch'),
    insufficient_evidence_count: num(raw, 'insufficient_evidence_count', 'demo batch'),
    auto_match_rate: parseRate(raw.auto_match_rate, 'demo batch auto-match rate'),
    exception_breakdown: list(raw, 'exception_breakdown', 'demo batch', parseDemoException),
    processing_duration_ms: num(raw, 'processing_duration_ms', 'demo batch'),
    throughput_lines_per_second: num(raw, 'throughput_lines_per_second', 'demo batch'),
    contract_agreement: parseRate(raw.contract_agreement, 'demo batch contract agreement'),
    exception_recall: parseRate(raw.exception_recall, 'demo batch exception recall'),
    false_resolution_rate: parseRate(raw.false_resolution_rate, 'demo batch false-resolution rate'),
    generator_version: str(raw, 'generator_version', 'demo batch'),
    harness_version: str(raw, 'harness_version', 'demo batch'),
    baseline_version: str(raw, 'baseline_version', 'demo batch'),
    limitation: str(raw, 'limitation', 'demo batch'),
  };
}
