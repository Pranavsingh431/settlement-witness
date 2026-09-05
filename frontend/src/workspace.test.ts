import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  prepareSampleWorkspace,
  SAMPLE_SOURCES,
  money,
  findingTitle,
  evidenceRequestText,
  downloadText,
} from './workspace';
import {
  ACCEPTED_RECEIPT,
  DUPLICATE_RECEIPT,
  INVALID_RECEIPT,
  RUN,
  EXCEPTION_DECISION,
  RESOLVED_DECISION,
} from './test/fixtures';
vi.mock('./api/client');
const client = vi.mocked(await import('./api/client'));

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('synthetic,csv\n1,2')));
  client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
  client.createRun.mockResolvedValue({ run: RUN, created: true });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('sample workspace through the real front doors', () => {
  it('imports every fixed source with its explicit identity before reconciling', async () => {
    // A real Response body can only be consumed once: give each file its own response.
    vi.mocked(fetch).mockImplementation(() => Promise.resolve(new Response('synthetic,csv\n1,2')));
    const progress = vi.fn();
    expect(await prepareSampleWorkspace(progress)).toEqual(RUN);
    expect(client.importDocument).toHaveBeenCalledTimes(4);
    for (const [index, source] of SAMPLE_SOURCES.entries()) {
      const call = client.importDocument.mock.calls[index];
      expect(call?.[0].name).toBe(source.file);
      expect(call?.slice(1)).toEqual([source.source, source.type]);
      expect(fetch).toHaveBeenNthCalledWith(index + 1, `/samples/${source.file}`);
    }
    expect(client.createRun).toHaveBeenCalledOnce();
    expect(progress).toHaveBeenLastCalledWith('Matching payments to settlements…');
  });
  it('safely reuses duplicate sample imports', async () => {
    vi.mocked(fetch).mockImplementation(() => Promise.resolve(new Response('csv')));
    client.importDocument.mockResolvedValue(DUPLICATE_RECEIPT);
    await expect(prepareSampleWorkspace(vi.fn())).resolves.toEqual(RUN);
  });
  it('stops at a download failure and never reconciles a partial batch', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('', { status: 503 }));
    await expect(prepareSampleWorkspace(vi.fn())).rejects.toMatchObject({
      code: 'sample_unavailable',
    });
    expect(client.importDocument).not.toHaveBeenCalled();
    expect(client.createRun).not.toHaveBeenCalled();
  });
  it('reports saved earlier imports without claiming rollback', async () => {
    vi.mocked(fetch).mockImplementation(() => Promise.resolve(new Response('csv')));
    client.importDocument
      .mockResolvedValueOnce(ACCEPTED_RECEIPT)
      .mockResolvedValueOnce(INVALID_RECEIPT);
    await expect(prepareSampleWorkspace(vi.fn())).rejects.toThrow(/Earlier imports are saved/);
    expect(client.importDocument).toHaveBeenCalledTimes(2);
    expect(client.createRun).not.toHaveBeenCalled();
  });
});

describe('human-readable amounts and requests', () => {
  it.each([
    ['INR', 12345, '123.45'],
    ['JPY', 12345, '12,345'],
    ['KWD', 12345, '12.345'],
    ['USD', -100, '1.00'],
  ])('preserves %s minor-unit precision', (currency, value, expected) => {
    expect(money(value, currency)).toContain(expected);
    expect(money(value, currency)).toContain(currency);
  });
  it('does not pretend to know the scale of an unknown currency', () => {
    expect(money(12345, 'ZZZ')).toBe('ZZZ 12,345 minor units');
  });
  it('uses a plain-language title and a truthful fallback', () => {
    expect(findingTitle(RESOLVED_DECISION)).toBe('Payment and settlement agree');
    expect(findingTitle(EXCEPTION_DECISION)).toBe('Partial refund to review');
    expect(findingTitle({ ...EXCEPTION_DECISION, exception_codes: ['NEW_CODE'] })).toBe('New code');
    expect(findingTitle({ ...EXCEPTION_DECISION, exception_codes: [], status: 'PENDING' })).toBe(
      'Waiting for processing',
    );
    expect(findingTitle({ ...EXCEPTION_DECISION, exception_codes: [] })).toBe(
      'Supporting records needed',
    );
  });
  it('exports every proof obligation, snapshot and non-authority condition', () => {
    const first = EXCEPTION_DECISION.closure_plan.actions[0];
    if (!first) throw new Error('Action fixture missing');
    const decision = {
      ...EXCEPTION_DECISION,
      closure_plan: {
        ...EXCEPTION_DECISION.closure_plan,
        actions: [
          first,
          { ...first, title: 'Second independent finding', supported_by_current_contract: true },
        ],
      },
    };
    const text = evidenceRequestText(RUN, decision);
    expect(text).toContain(RUN.snapshot_fingerprint);
    expect(text).toContain(first.evidence_required);
    expect(text).toContain('Second independent finding');
    expect(text).toContain('Verification available: Yes');
    expect(text).toContain('Requires a new evidence rule');
    expect(text).toContain('not an approval');
    expect(text).toContain(decision.closure_plan.resolution_gate);
  });
  it('downloads plain text and releases only its own blob URL afterwards', () => {
    vi.useFakeTimers();
    const revoke = vi.fn();
    vi.stubGlobal(
      'URL',
      class extends URL {
        static override createObjectURL = vi.fn(() => 'blob:request');
        static override revokeObjectURL = revoke;
      },
    );
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      expect(this.download).toBe('request.txt');
      expect(this.href).toBe('blob:request');
    });
    downloadText('request.txt', 'No approval');
    expect(click).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:request');
  });
});
