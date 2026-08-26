/**
 * Tests for the API client.
 *
 * `fetch` is replaced rather than a server being started, so each test states
 * exactly what the backend returned. The payloads are real ones, copied from a
 * running backend into the fixtures module.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ACCEPTED_RECEIPT, EXCEPTION_DECISION, RESOLVED_DECISION, RUN } from '../test/fixtures';
import { createRun, getImport, getRun, importDocument, listImports, listRuns } from './client';
import { ApiError, MalformedResponseError, NetworkError, describeError } from './errors';

const fetchMock = vi.fn();

function answer(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

function rawAnswer(text: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(text),
  } as unknown as Response;
}

/** The nested envelope every failure on this API arrives in. */
function envelope(error: string, detail: string): unknown {
  return { detail: { error, detail } };
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('addressing the backend', () => {
  it('asks for a same-origin relative path', async () => {
    fetchMock.mockResolvedValue(answer({ runs: [], total: 0, limit: 20, offset: 0 }));

    await listRuns();

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/v1/reconciliation/runs');
  });

  it('names no host anywhere', async () => {
    fetchMock.mockResolvedValue(answer({ runs: [], total: 0, limit: 20, offset: 0 }));

    await listRuns({ limit: 5 });

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url.startsWith('/')).toBe(true);
    expect(url).not.toContain('localhost');
    expect(url).not.toContain('http');
  });

  it('leaves an unset filter out of the query rather than sending it empty', async () => {
    fetchMock.mockResolvedValue(
      answer({ receipts: [], total: 0, limit: 20, offset: 0, filtered: false }),
    );

    await listImports({ limit: 5, outcome: undefined, source_system: '' });

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/v1/imports?limit=5');
  });

  it('escapes an identifier that would otherwise change the path', async () => {
    fetchMock.mockResolvedValue(answer({ run: RUN, decisions: [], filtered: false }));

    await getRun('a/b?c');

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/v1/reconciliation/runs/a%2Fb%3Fc');
  });
});

describe('reading runs', () => {
  it('returns a page of runs', async () => {
    fetchMock.mockResolvedValue(answer({ runs: [RUN], total: 1, limit: 20, offset: 0 }));

    const page = await listRuns();

    expect(page.total).toBe(1);
    expect(page.runs[0]?.run_id).toBe(RUN.run_id);
    expect(page.runs[0]?.status_counts.RESOLVED).toBe(1);
  });

  it('returns a run with its decisions', async () => {
    fetchMock.mockResolvedValue(
      answer({ run: RUN, decisions: [RESOLVED_DECISION, EXCEPTION_DECISION], filtered: false }),
    );

    const detail = await getRun(RUN.run_id);

    expect(detail.decisions).toHaveLength(2);
    expect(detail.decisions[0]?.invariant_results[0]?.outcome).toBe('PASSED');
    expect(detail.filtered).toBe(false);
  });

  it('passes decision filters through as query parameters', async () => {
    fetchMock.mockResolvedValue(
      answer({ run: RUN, decisions: [EXCEPTION_DECISION], filtered: true }),
    );

    await getRun(RUN.run_id, { status: 'EXCEPTION', exception_code: 'PARTIAL_REFUND' });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/v1/reconciliation/runs/${RUN.run_id}?status=EXCEPTION&exception_code=PARTIAL_REFUND`,
    );
  });
});

describe('creating a run', () => {
  it('reports 201 as a newly recorded run', async () => {
    fetchMock.mockResolvedValue(answer(RUN, 201));

    const result = await createRun();

    expect(result.created).toBe(true);
    expect(result.run.run_id).toBe(RUN.run_id);
  });

  it('reports 200 as a run that already existed', async () => {
    fetchMock.mockResolvedValue(answer(RUN, 200));

    const result = await createRun();

    expect(result.created).toBe(false);
    expect(result.run.run_id).toBe(RUN.run_id);
  });

  it('uses POST', async () => {
    fetchMock.mockResolvedValue(answer(RUN, 201));

    await createRun();

    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: 'POST' });
  });

  it('surfaces the refusal when there is nothing to reconcile', async () => {
    fetchMock.mockResolvedValue(
      answer(envelope('no_facts', 'the store holds no accepted source facts to reconcile'), 409),
    );

    await expect(createRun()).rejects.toThrow(ApiError);
    await expect(createRun()).rejects.toMatchObject({
      status: 409,
      code: 'no_facts',
      message: 'the store holds no accepted source facts to reconcile',
    });
  });
});

describe('importing a document', () => {
  it('sends the file and both declared fields as form data', async () => {
    fetchMock.mockResolvedValue(answer(ACCEPTED_RECEIPT, 201));
    const file = new File(['a,b\n1,2\n'], 'payouts.csv', { type: 'text/csv' });

    await importDocument(file, 'PSP_API', 'PAYOUT');

    const call = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(call[0]).toBe('/v1/imports');
    const body = call[1].body as FormData;
    expect(body.get('source_system')).toBe('PSP_API');
    expect(body.get('record_type')).toBe('PAYOUT');
    expect(body.get('file')).toBe(file);
  });

  it('returns the receipt the server recorded', async () => {
    fetchMock.mockResolvedValue(answer(ACCEPTED_RECEIPT, 201));
    const file = new File(['a'], 'payouts.csv');

    const receipt = await importDocument(file, 'PSP_API', 'PAYOUT');

    expect(receipt.outcome).toBe('ACCEPTED');
    expect(receipt.wrote_facts).toBe(true);
    expect(receipt.row_outcomes).toHaveLength(2);
  });

  it('surfaces a document that was refused for being too large', async () => {
    fetchMock.mockResolvedValue(
      answer(
        envelope(
          'document_too_large',
          'the uploaded document is larger than the 8388608 byte limit; no import was processed and no receipt was written',
        ),
        413,
      ),
    );
    const file = new File(['a'], 'big.csv');

    await expect(importDocument(file, 'PSP_API', 'PAYOUT')).rejects.toMatchObject({
      status: 413,
      code: 'document_too_large',
    });
  });

  it('returns a page of receipts', async () => {
    fetchMock.mockResolvedValue(
      answer({ receipts: [ACCEPTED_RECEIPT], total: 1, limit: 20, offset: 0, filtered: false }),
    );

    const page = await listImports();

    expect(page.receipts[0]?.receipt_id).toBe(ACCEPTED_RECEIPT.receipt_id);
    expect(page.filtered).toBe(false);
  });

  it('returns one receipt by its identifier', async () => {
    fetchMock.mockResolvedValue(answer(ACCEPTED_RECEIPT));

    const receipt = await getImport(ACCEPTED_RECEIPT.receipt_id);

    expect(receipt.document_name).toBe('payment_events.csv');
  });
});

describe('failures', () => {
  it('reads the nested error envelope', async () => {
    fetchMock.mockResolvedValue(
      answer(envelope('not_found', "no import receipt with id 'nope'"), 404),
    );

    await expect(getImport('nope')).rejects.toMatchObject({
      status: 404,
      code: 'not_found',
      message: "no import receipt with id 'nope'",
    });
  });

  it('falls back to the status when the envelope is not the expected shape', async () => {
    fetchMock.mockResolvedValue(answer({ something: 'else' }, 500));

    await expect(listRuns()).rejects.toMatchObject({
      status: 500,
      message: 'The backend refused the request with status 500.',
    });
  });

  it('reads a bare detail string, which is what a proxy may return', async () => {
    fetchMock.mockResolvedValue(answer({ detail: 'Not Found' }, 404));

    await expect(listRuns()).rejects.toMatchObject({ status: 404, message: 'Not Found' });
  });

  it('does not choke on an error body that is not JSON at all', async () => {
    fetchMock.mockResolvedValue(rawAnswer('<html>502 Bad Gateway</html>', 502));

    await expect(listRuns()).rejects.toMatchObject({
      status: 502,
      message: 'The backend refused the request with status 502.',
    });
  });

  it('reports an unreachable backend as a network error', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(listRuns()).rejects.toThrow(NetworkError);
  });

  it('refuses a success body that is not JSON', async () => {
    fetchMock.mockResolvedValue(rawAnswer('not json at all', 200));

    await expect(listRuns()).rejects.toThrow(MalformedResponseError);
  });

  it('refuses a success body missing a field the interface reads', async () => {
    fetchMock.mockResolvedValue(answer({ runs: [], total: 0, limit: 20 }));

    await expect(listRuns()).rejects.toThrow(MalformedResponseError);
  });

  it('refuses a run whose counts are not numbers', async () => {
    fetchMock.mockResolvedValue(answer({ ...RUN, status_counts: { RESOLVED: 'one' } }, 201));

    await expect(createRun()).rejects.toThrow(MalformedResponseError);
  });

  it('refuses a decision list holding something that is not a decision', async () => {
    fetchMock.mockResolvedValue(answer({ run: RUN, decisions: ['nope'], filtered: false }));

    await expect(getRun('r')).rejects.toThrow(MalformedResponseError);
  });

  it('names the field it could not read', async () => {
    fetchMock.mockResolvedValue(answer({ runs: [], total: 0, limit: 20 }));

    await expect(listRuns()).rejects.toThrow(/offset/);
  });
});

describe('describing a failure for a person', () => {
  it('passes an API message through as the backend wrote it', () => {
    expect(describeError(new ApiError(409, 'no_facts', 'nothing to reconcile'))).toBe(
      'nothing to reconcile',
    );
  });

  it('explains an unreachable backend and does not mention fetch', () => {
    const message = describeError(new NetworkError());

    expect(message).toContain('could not be reached');
    expect(message).not.toContain('fetch');
  });

  it('explains a malformed response', () => {
    expect(describeError(new MalformedResponseError('run is not an object'))).toContain(
      'could not read',
    );
  });

  it('says something useful about an error it does not recognise', () => {
    expect(describeError(new Error('kaboom at line 42'))).toBe(
      'Something went wrong. Please try again.',
    );
  });
});
