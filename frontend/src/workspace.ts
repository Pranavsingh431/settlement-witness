import { createRun, importDocument } from './api/client';
import { ApiError } from './api/errors';
import type { ClosureLane, DecisionView, RunSummary } from './api/types';
import { humanise, formatMinorUnits } from './format';

export const SAMPLE_SOURCES = [
  {
    file: 'payment_events.csv',
    type: 'PAYMENT_EVENT',
    source: 'PSP_API',
    label: 'Payments',
    rows: 65,
  },
  {
    file: 'settlement_lines.csv',
    type: 'SETTLEMENT_LINE',
    source: 'PSP_API',
    label: 'Settlements',
    rows: 59,
  },
  { file: 'payouts.csv', type: 'PAYOUT', source: 'PSP_API', label: 'Payouts', rows: 56 },
  {
    file: 'bank_transactions.csv',
    type: 'BANK_TRANSACTION',
    source: 'BANK_STATEMENT',
    label: 'Bank statement',
    rows: 56,
  },
] as const;

/** A fixed sample workflow through the same importer used for operator uploads.
 * Each document commits atomically. A later failure leaves earlier receipts visible;
 * replaying accepted sample documents is handled by the importer's duplicate rule.
 */
export async function prepareSampleWorkspace(
  progress: (step: string) => void,
): Promise<RunSummary> {
  for (const source of SAMPLE_SOURCES) {
    progress(`Importing ${source.label.toLowerCase()}…`);
    const response = await fetch(`/samples/${source.file}`);
    if (!response.ok)
      throw new ApiError(
        response.status,
        'sample_unavailable',
        `Could not load the sample ${source.label.toLowerCase()}. Try again.`,
      );
    const file = new File([await response.blob()], source.file, { type: 'text/csv' });
    const receipt = await importDocument(file, source.source, source.type);
    if (receipt.outcome !== 'ACCEPTED' && receipt.outcome !== 'DUPLICATE_NO_OP') {
      throw new ApiError(
        422,
        'sample_rejected',
        `${source.label} could not be imported. Earlier imports are saved. Open Data sources to inspect the receipt before retrying.`,
      );
    }
  }
  progress('Matching payments to settlements…');
  return (await createRun()).run;
}

export const TEAM_LABELS: Record<ClosureLane, string> = {
  NONE: 'No follow-up',
  EVIDENCE_OPERATIONS: 'Evidence team',
  PSP_OPERATIONS: 'Payment provider',
  DATA_QUALITY: 'Data operations',
  FINANCE_CONTROL: 'Finance team',
};

const FINDINGS: Record<string, string> = {
  PARTIAL_REFUND: 'Partial refund to review',
  AMOUNT_MISMATCH: 'Amounts do not agree',
  MISSING_PAYOUT: 'Payout record missing',
  MISSING_PAYMENT: 'Payment record missing',
  MISSING_CAPTURE: 'Capture record missing',
  MISSING_SETTLEMENT: 'Settlement record missing',
  CURRENCY_MISMATCH: 'Currencies do not agree',
  OUT_OF_ORDER_EVENT: 'Events arrived out of order',
  UNSUPPORTED_STATE: 'Finance review needed',
  DUPLICATE_EVENT: 'Duplicate event',
  PAYOUT_MISMATCH: 'Payout total does not agree',
  FEE_MISMATCH: 'Fees do not agree',
};

export function findingTitle(decision: DecisionView): string {
  const first = decision.exception_codes[0];
  if (first) return FINDINGS[first] ?? humanise(first);
  if (decision.status === 'RESOLVED') return 'Payment and settlement agree';
  if (decision.status === 'PENDING') return 'Waiting for processing';
  return 'Supporting records needed';
}

/** ISO minor-unit precision from Intl; unknown currencies stay explicitly in minor units. */
export function money(value: number, currency: string): string {
  if (!Intl.supportedValuesOf('currency').includes(currency))
    return `${currency} ${formatMinorUnits(value)} minor units`;
  const formatter = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    currencyDisplay: 'code',
  });
  const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  return formatter.format(value / 10 ** digits);
}

export function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  // Release after the browser has started the download, not before it reads the URL.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
}

export function evidenceRequestText(run: RunSummary, decision: DecisionView): string {
  return [
    'SETTLEMENT WITNESS · EVIDENCE REQUEST',
    '',
    findingTitle(decision),
    `Settlement: ${decision.subject_settlement_line_id}`,
    `Run: ${run.run_id}`,
    `Snapshot: ${run.snapshot_fingerprint}`,
    `Baseline status: ${decision.status}`,
    `Suggested team: ${TEAM_LABELS[decision.closure_plan.primary_owner]}`,
    '',
    ...decision.closure_plan.actions.flatMap((action, index) => [
      `${String(index + 1)}. ${action.title}`,
      action.instruction,
      `Please provide: ${action.evidence_required}`,
      `Verification available: ${action.supported_by_current_contract ? 'Yes' : 'Requires a new evidence rule'}`,
      '',
    ]),
    'Acceptance condition',
    decision.closure_plan.resolution_gate,
    '',
    'This is a request for supporting records. It is not an approval or a financial adjustment.',
    'Import returned evidence and reconcile again. The original decision remains recorded.',
  ].join('\n');
}
