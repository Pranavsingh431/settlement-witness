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
 * All four the contract defines, since Phase 12 gave `BANK_TRANSACTION` a
 * layout. A bank statement is the only document here the payment provider did
 * not write, and it is the only evidence that can say a payout arrived.
 */
export const IMPORTABLE_RECORD_TYPES = [
  'PAYMENT_EVENT',
  'SETTLEMENT_LINE',
  'PAYOUT',
  'BANK_TRANSACTION',
] as const;
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

/** One fixed fixture document prepared by the public walkthrough. */
export interface DemoFixtureResult {
  readonly document_name: string;
  readonly source_record_type: ImportableRecordType;
  readonly outcome: ImportOutcome;
  /** True only when this request added the fixture's accepted source facts. */
  readonly loaded_now: boolean;
}

/** The conclusions prepared by the bundled, synthetic walkthrough. */
export interface DemoBootstrapResult {
  readonly fixture_results: readonly DemoFixtureResult[];
  readonly run: RunSummary;
  readonly bank_finality_audit: BankFinalityAuditSummary;
  /** True when this request added fixtures or recorded a conclusion. */
  readonly created: boolean;
}

/**
 * The four things a reviewer may record, and nothing else.
 *
 * There is no approve, no resolve and no override. `CLOSED_WITHOUT_OVERRIDE` is
 * named the way it is because the name is the guarantee: an item can leave the
 * working queue, and the line it points at is still whatever the baseline found
 * it to be.
 */
export const REVIEW_ACTIONS = [
  'ACKNOWLEDGED',
  'REQUEST_EVIDENCE',
  'ESCALATED',
  'CLOSED_WITHOUT_OVERRIDE',
] as const;
export type ReviewAction = (typeof REVIEW_ACTIONS)[number];

/**
 * Where an item stands operationally.
 *
 * Deliberately no value in common with `DecisionStatus`. A workflow state and a
 * baseline status are different facts about different things, and a shared word
 * would be the beginning of a screen that showed one where the other belongs.
 */
export type ReviewWorkflowState =
  'OPEN' | 'ACKNOWLEDGED' | 'WAITING_FOR_EVIDENCE' | 'ESCALATED' | 'CLOSED_WITHOUT_OVERRIDE';

export interface ReviewEventView {
  readonly event_id: string;
  /** The order the database assigned. What the timeline is sorted by. */
  readonly sequence: number;
  readonly action: ReviewAction;
  /** A sentence from a person. Rendered as text, never as markup. */
  readonly note: string | null;
  readonly recorded_at: string;
  readonly decision_fingerprint: string;
}

export interface ReviewQueueItem {
  readonly run_id: string;
  /** The baseline's conclusion, unchanged by anything in this response. */
  readonly decision: DecisionView;
  /** Echo this back when appending an event, so a stale command is refused. */
  readonly decision_fingerprint: string;
  readonly workflow_state: ReviewWorkflowState;
  /** The same value as `decision.status`, named so it cannot be missed. */
  readonly baseline_status: DecisionStatus;
  readonly baseline_unchanged_note: string;
  readonly events: readonly ReviewEventView[];
}

export interface ReviewQueuePage {
  readonly run_id: string;
  readonly review_contract_version: string;
  readonly items: readonly ReviewQueueItem[];
  /** How many reviewable decisions the run holds, not how many it has. */
  readonly total: number;
  /** How many of those are not closed. */
  readonly open_total: number;
  readonly limit: number;
  readonly offset: number;
  readonly baseline_unchanged_note: string;
}

export interface ReviewEventReceipt {
  readonly event: ReviewEventView;
  readonly workflow_state: ReviewWorkflowState;
  /** The status after the event, which is the status before it. */
  readonly baseline_status: DecisionStatus;
  readonly baseline_unchanged_note: string;
}

/**
 * What the records say about one payout reaching the bank.
 *
 * Seven outcomes and no maybe. Deliberately sharing no value with
 * `DecisionStatus`: a settlement decision and a bank finality outcome are
 * different conclusions from different evidence, and a shared word would be the
 * beginning of a screen that showed one where the other belongs.
 */
export type BankFinalityOutcome =
  | 'VERIFIED_BANK_CREDIT'
  | 'MISSING_BANK_EVIDENCE'
  | 'UNLINKABLE_PAYOUT'
  | 'AMBIGUOUS_BANK_EVIDENCE'
  | 'BANK_DIRECTION_MISMATCH'
  | 'BANK_AMOUNT_MISMATCH'
  | 'BANK_CURRENCY_MISMATCH';

export type BankDirection = 'CREDIT' | 'DEBIT';

export interface BankFinalityCertificate {
  readonly payout_id: string;
  readonly payout_source_record_id: string;
  /** Null when the payout declared none, which is what makes it unlinkable. */
  readonly bank_reference: string | null;
  readonly outcome: BankFinalityOutcome;
  readonly evidence: readonly EvidenceReference[];
  readonly matched_bank_transaction_ids: readonly string[];
  readonly expected_amount_minor: number | null;
  readonly expected_currency: string | null;
  readonly observed_amount_minor: number | null;
  readonly observed_currency: string | null;
  readonly observed_direction: BankDirection | null;
  readonly recorded_at: string;
  readonly schema_version: string;
}

export interface BankFinalityAuditSummary {
  readonly audit_id: string;
  /** The same digest the reconciliation run over these facts carries. */
  readonly snapshot_fingerprint: string;
  readonly bank_finality_version: string;
  readonly bank_statement_schema_version: string;
  readonly created_at: string;
  readonly as_of: string;
  readonly fact_count: number;
  readonly payout_count: number;
  readonly bank_transaction_count: number;
  readonly outcome_counts: Readonly<Record<string, number>>;
  /** A count, never a rate. See the API docs for why. */
  readonly verified_payout_count: number;
}

export interface BankFinalityAuditDetail {
  readonly audit: BankFinalityAuditSummary;
  readonly certificates: readonly BankFinalityCertificate[];
  readonly filtered: boolean;
  readonly settlement_and_finality_are_separate: string;
}

export interface BankFinalityAuditPage {
  readonly audits: readonly BankFinalityAuditSummary[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly filtered: boolean;
  readonly bank_finality_version: string;
  readonly settlement_and_finality_are_separate: string;
}

/**
 * The result of asking for a bank finality audit.
 *
 * 201 means a new immutable audit was recorded. 200 means an identical snapshot
 * under the same bank finality rules already had one and it was returned rather
 * than duplicated. Callers need to tell those apart, so the status is carried
 * out rather than flattened into "it worked".
 */
export interface BankFinalityAuditCreation {
  readonly audit: BankFinalityAuditSummary;
  readonly created: boolean;
}
