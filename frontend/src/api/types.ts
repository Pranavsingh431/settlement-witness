/**
 * The shapes the backend actually publishes.
 *
 * Mirrors `docs/api.md` and the OpenAPI schema. Written out rather than
 * generated so that a change on either side shows up as a type error here
 * instead of as a blank cell in a table.
 */

export const DECISION_STATUSES = [
  'RESOLVED',
  'EXCEPTION',
  'PENDING',
  'INSUFFICIENT_EVIDENCE',
] as const;
export type DecisionStatus = (typeof DECISION_STATUSES)[number];

export const IMPORT_OUTCOMES = [
  'ACCEPTED',
  'DUPLICATE_NO_OP',
  'REJECTED_CONFLICT',
  'REJECTED_INVALID',
] as const;
export type ImportOutcome = (typeof IMPORT_OUTCOMES)[number];

export const ROW_OUTCOMES = [
  'ACCEPTED',
  'DUPLICATE_NO_OP',
  'DUPLICATE_CONFLICT',
  'REJECTED',
  'NOT_APPLIED',
] as const;
export type RowOutcome = (typeof ROW_OUTCOMES)[number];

export const SOURCE_SYSTEMS = [
  'PSP_API',
  'PSP_WEBHOOK',
  'BANK_STATEMENT',
  'MERCHANT_LEDGER',
] as const;
export type SourceSystem = (typeof SOURCE_SYSTEMS)[number];

/**
 * The record types the CSV parser has a schema for.
 *
 * `BANK_TRANSACTION` is a valid source record type in the domain contract and
 * the parser has no schema for it, so the import form must not offer it. The
 * server refuses it with 422 either way; not offering it is what stops a person
 * choosing it and being told no.
 */
export const IMPORTABLE_RECORD_TYPES = ['PAYMENT_EVENT', 'SETTLEMENT_LINE', 'PAYOUT'] as const;
export type ImportableRecordType = (typeof IMPORTABLE_RECORD_TYPES)[number];

export type EvidenceOutcome =
  'VERIFIED' | 'FACT_NOT_FOUND' | 'SOURCE_SYSTEM_MISMATCH' | 'PAYLOAD_HASH_MISMATCH';

export type InvariantOutcome = 'PASSED' | 'FAILED' | 'NOT_APPLICABLE' | 'INSUFFICIENT_INPUT';

export interface EvidenceReference {
  readonly source_record_id: string;
  readonly source_system: string;
  readonly payload_hash: string;
  /** Null when the decision carries no verification result for this citation. */
  readonly verification_outcome: EvidenceOutcome | null;
}

export interface InvariantCheck {
  readonly invariant_id: string;
  readonly outcome: InvariantOutcome;
  readonly reason_code: string | null;
  /** Minor units. The API sends no currency, so neither does the interface. */
  readonly expected_minor: number | null;
  readonly observed_minor: number | null;
}

export interface DecisionView {
  readonly decision_id: string;
  readonly schema_version: string;
  readonly status: DecisionStatus;
  readonly subject_settlement_line_id: string;
  readonly linked_source_record_ids: readonly string[];
  readonly linked_event_ids: readonly string[];
  readonly evidence: readonly EvidenceReference[];
  readonly invariant_results: readonly InvariantCheck[];
  readonly exception_codes: readonly string[];
  readonly reason_codes: readonly string[];
  readonly created_at: string;
  readonly verified_evidence_count: number;
}

export interface RunSummary {
  readonly run_id: string;
  readonly snapshot_fingerprint: string;
  readonly baseline_version: string;
  readonly domain_schema_version: string;
  readonly parser_version: string;
  readonly created_at: string;
  readonly as_of: string;
  readonly fact_count: number;
  readonly settlement_line_count: number;
  readonly decision_count: number;
  readonly status_counts: Readonly<Record<string, number>>;
  readonly exception_counts: Readonly<Record<string, number>>;
}

export interface RunPage {
  readonly runs: readonly RunSummary[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface RunDetail {
  readonly run: RunSummary;
  readonly decisions: readonly DecisionView[];
  /** True when a status or exception filter narrowed the list. */
  readonly filtered: boolean;
}

export interface RowOutcomeView {
  readonly row_number: number;
  readonly outcome: RowOutcome;
  readonly source_record_id: string | null;
  readonly code: string | null;
  readonly detail: string | null;
}

export interface ImportReceipt {
  readonly receipt_id: string;
  readonly document_hash: string;
  readonly document_name: string;
  readonly source_system: string;
  readonly source_record_type: string;
  readonly parser_version: string;
  readonly received_at: string;
  readonly outcome: ImportOutcome;
  readonly row_count: number;
  readonly accepted_count: number;
  readonly duplicate_count: number;
  readonly conflict_count: number;
  readonly rejected_count: number;
  readonly not_applied_count: number;
  /** Derived from `accepted_count` by the server, and validated against it there. */
  readonly wrote_facts: boolean;
  readonly failure_detail: string | null;
  readonly row_outcomes: readonly RowOutcomeView[];
}

export interface ImportReceiptPage {
  readonly receipts: readonly ImportReceipt[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  /** True when a filter narrowed the list, so `total` is not the whole history. */
  readonly filtered: boolean;
}

/**
 * The result of asking for a run.
 *
 * The API answers 201 when it recorded a new run and 200 when an identical
 * snapshot already had one. That distinction is the whole point of the run key,
 * so it is carried rather than flattened into "it worked".
 */
export interface RunCreation {
  readonly run: RunSummary;
  readonly created: boolean;
}
