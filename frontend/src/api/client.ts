/**
 * The one place this app talks to the backend.
 *
 * Every request goes to a same-origin relative path. There is no host in this
 * file and no environment variable holding one: in development Vite proxies
 * `/v1` to the backend, and in the container nginx does. A hard-coded
 * `localhost` would work on the machine it was written on and nowhere else, and
 * would need permissive CORS on the backend to work at all, which is a real
 * loosening of the server in exchange for a convenience in the client.
 *
 * Responses are checked before they are returned. Nothing here casts.
 */

import { MalformedResponseError, NetworkError, toApiError } from './errors';
import {
  parseReceipt,
  parseReceiptPage,
  parseReviewEventReceipt,
  parseReviewQueueItem,
  parseReviewQueuePage,
  parseRunDetail,
  parseRunPage,
  parseRunSummary,
} from './parse';
import type {
  ImportReceipt,
  ImportReceiptPage,
  ReviewAction,
  ReviewEventReceipt,
  ReviewQueueItem,
  ReviewQueuePage,
  RunCreation,
  RunDetail,
  RunPage,
} from './types';

const IMPORTS = '/v1/imports';
const RUNS = '/v1/reconciliation/runs';
const REVIEW = '/v1/review/runs';

interface Answer {
  readonly status: number;
  readonly body: unknown;
}

/**
 * Send one request and read its body, whatever the outcome.
 *
 * The body is read for a failure as well as a success, because the error
 * envelope is where the message written for people lives.
 */
async function send(path: string, init?: RequestInit): Promise<Answer> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    throw new NetworkError(cause);
  }

  let body: unknown = null;
  const text = await response.text();
  if (text.length > 0) {
    try {
      body = JSON.parse(text);
    } catch {
      if (response.ok) {
        throw new MalformedResponseError('the body was not JSON');
      }
    }
  }

  if (!response.ok) {
    throw toApiError(response.status, body);
  }
  return { status: response.status, body };
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered.length > 0 ? `?${rendered}` : '';
}

/**
 * Filters for the receipt list.
 *
 * Every field allows `undefined` explicitly, because "no filter" is a value the
 * screens pass around rather than a key they conditionally leave out.
 */
export interface ImportFilters {
  readonly limit?: number | undefined;
  readonly offset?: number | undefined;
  readonly outcome?: string | undefined;
  readonly source_system?: string | undefined;
  readonly record_type?: string | undefined;
}

/** Return a page of import receipts, newest attempt first. */
export async function listImports(filters: ImportFilters = {}): Promise<ImportReceiptPage> {
  const { body } = await send(`${IMPORTS}${query({ ...filters })}`);
  return parseReceiptPage(body);
}

/** Return one import receipt in full. */
export async function getImport(receiptId: string): Promise<ImportReceipt> {
  const { body } = await send(`${IMPORTS}/${encodeURIComponent(receiptId)}`);
  return parseReceipt(body);
}

/**
 * Upload one CSV document and return the receipt the server recorded.
 *
 * The source system and the record type are sent as the caller declared them.
 * Neither is taken from the file name or the headers, here or on the server: a
 * document read as the wrong record type fails loudly, and one read as the
 * wrong source system would import cleanly and be wrong.
 */
export async function importDocument(
  file: File,
  sourceSystem: string,
  recordType: string,
): Promise<ImportReceipt> {
  const form = new FormData();
  form.append('file', file);
  form.append('source_system', sourceSystem);
  form.append('record_type', recordType);
  const { body } = await send(IMPORTS, { method: 'POST', body: form });
  return parseReceipt(body);
}

export interface RunListFilters {
  readonly limit?: number | undefined;
  readonly offset?: number | undefined;
}

/** Return a page of reconciliation runs, newest first. */
export async function listRuns(filters: RunListFilters = {}): Promise<RunPage> {
  const { body } = await send(`${RUNS}${query({ ...filters })}`);
  return parseRunPage(body);
}

/**
 * Reconcile the stored facts and return the run, saying whether it is new.
 *
 * 201 means a new immutable run was recorded. 200 means an identical snapshot
 * under the same rule versions already had one and it was returned rather than
 * duplicated. Callers need to tell those apart, so the status is carried out of
 * here rather than being flattened into a success.
 */
export async function createRun(): Promise<RunCreation> {
  const { status, body } = await send(RUNS, { method: 'POST' });
  return { run: parseRunSummary(body), created: status === 201 };
}

export interface DecisionFilters {
  readonly status?: string | undefined;
  readonly exception_code?: string | undefined;
}

/** Return one run with its decisions, optionally narrowed. */
export async function getRun(runId: string, filters: DecisionFilters = {}): Promise<RunDetail> {
  const { body } = await send(`${RUNS}/${encodeURIComponent(runId)}${query({ ...filters })}`);
  return parseRunDetail(body);
}

export interface ReviewQueueFilters {
  readonly limit?: number | undefined;
  readonly offset?: number | undefined;
}

/**
 * Return a page of the review queue for one recorded run.
 *
 * Only the decisions that need a person are here. A resolved line is not work,
 * and the server leaves it out rather than expecting the client to filter.
 */
export async function getReviewQueue(
  runId: string,
  filters: ReviewQueueFilters = {},
): Promise<ReviewQueuePage> {
  const { body } = await send(
    `${REVIEW}/${encodeURIComponent(runId)}/queue${query({ ...filters })}`,
  );
  return parseReviewQueuePage(body);
}

/** Return one queue item with its certificate and its review timeline. */
export async function getReviewItem(runId: string, decisionId: string): Promise<ReviewQueueItem> {
  const { body } = await send(
    `${REVIEW}/${encodeURIComponent(runId)}/queue/${encodeURIComponent(decisionId)}`,
  );
  return parseReviewQueueItem(body);
}

/**
 * Record one human review action beside a decision.
 *
 * The decision is untouched. There is no field here that could carry a status,
 * which is the point: an override is unexpressible rather than refused.
 *
 * `decisionFingerprint` is the one the server served with the item. Echoing it
 * back is what stops an action aimed at a conclusion the reviewer last saw
 * elsewhere being recorded against this one. `idempotencyKey` makes a retry a
 * retry: the same key with the same command returns the original event, and the
 * same key with a different command is refused.
 */
export async function appendReviewEvent(
  runId: string,
  decisionId: string,
  input: {
    readonly action: ReviewAction;
    readonly decisionFingerprint: string;
    readonly idempotencyKey: string;
    readonly note?: string | undefined;
  },
): Promise<ReviewEventReceipt> {
  const { body } = await send(
    `${REVIEW}/${encodeURIComponent(runId)}/queue/${encodeURIComponent(decisionId)}/events`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        action: input.action,
        decision_fingerprint: input.decisionFingerprint,
        idempotency_key: input.idempotencyKey,
        ...(input.note !== undefined && input.note !== '' ? { note: input.note } : {}),
      }),
    },
  );
  return parseReviewEventReceipt(body);
}
