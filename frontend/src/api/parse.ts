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
  DecisionView,
  EvidenceReference,
  ImportReceipt,
  ImportReceiptPage,
  InvariantCheck,
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
