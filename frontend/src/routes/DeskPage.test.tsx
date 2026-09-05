import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DeskPage } from './DeskPage';
import { renderScreen } from '../test/render';
import {
  RUN,
  WORKBOARD,
  BANK_AUDITS,
  NO_BANK_AUDITS,
  BANK_AUDIT,
  EXCEPTION_DECISION,
  RESOLVED_DECISION,
  INSUFFICIENT_DECISION,
} from '../test/fixtures';
import type { DecisionView } from '../api/types';
vi.mock('../api/client');
vi.mock('../workspace', async (original) => ({
  ...(await original<typeof import('../workspace')>()),
  prepareSampleWorkspace: vi.fn(),
  downloadText: vi.fn(),
}));
const client = vi.mocked(await import('../api/client'));
const workspace = vi.mocked(await import('../workspace'));
const decisions = [RESOLVED_DECISION, EXCEPTION_DECISION, INSUFFICIENT_DECISION];
beforeEach(() => {
  vi.resetAllMocks();
  client.listRuns.mockResolvedValue({ runs: [RUN], total: 1, limit: 1, offset: 0 });
  client.getRun.mockResolvedValue({ run: RUN, decisions, filtered: false });
  client.getWorkboard.mockResolvedValue(WORKBOARD);
  client.listBankFinalityAudits.mockResolvedValue(BANK_AUDITS);
  client.createRun.mockResolvedValue({ run: RUN, created: true });
  client.createBankFinalityAudit.mockResolvedValue({ audit: BANK_AUDIT, created: true });
  workspace.prepareSampleWorkspace.mockResolvedValue(RUN);
});
afterEach(() => vi.restoreAllMocks());
async function open(at = '/') {
  renderScreen(<DeskPage />, at);
  await screen.findByRole('region', { name: 'Batch overview' });
}

describe('the settlement desk', () => {
  it('shows actual recorded counts and queries bank evidence for the same snapshot', async () => {
    await open();
    expect(client.listBankFinalityAudits).toHaveBeenCalledWith({
      limit: 1,
      snapshot_fingerprint: RUN.snapshot_fingerprint,
    });
    expect(screen.getByText('33.3% match rate')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Partial refund to review' })).toBeInTheDocument();
    expect(screen.getByText(/INR.*10,000.00/)).toBeInTheDocument();
  });
  it('opens an explicit older run, not whichever happens to be newest', async () => {
    await open('/?run=older');
    expect(client.getRun).toHaveBeenCalledWith('older');
    expect(client.getWorkboard).toHaveBeenCalledWith('older');
  });
  it('takes an issue through the case panel and exact follow-up link', async () => {
    await open('/?view=issues');
    await userEvent.click(screen.getByRole('button', { name: 'Partial refund to review' }));
    const panel = screen.getByRole('complementary', { name: 'Case details' });
    expect(within(panel).getByRole('link', { name: /Record a follow-up/ })).toHaveAttribute(
      'href',
      `/runs/${RUN.run_id}/review?decision=${encodeURIComponent(EXCEPTION_DECISION.decision_id)}`,
    );
    expect(within(panel).getByText(/Requires a new evidence rule/)).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole('button', { name: /Download evidence request/ }));
    expect(workspace.downloadText).toHaveBeenCalledWith(
      'evidence-request.txt',
      expect.stringContaining('not an approval'),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Close case details' }));
    expect(screen.queryByRole('complementary', { name: 'Case details' })).not.toBeInTheDocument();
  });
  it('opens a case from its link and keeps all matched lines inspectable', async () => {
    await open(`/?case=${RESOLVED_DECISION.decision_id}`);
    expect(screen.getByRole('heading', { name: 'No follow-up needed' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Record a follow-up/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Matched' }));
    expect(
      screen.getByRole('button', { name: 'Payment and settlement agree' }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'All lines' }));
    expect(screen.getAllByRole('button', { name: /^Open line/ })).toHaveLength(3);
  });
  it('searches human titles, teams and identifiers and resets the page', async () => {
    await open();
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Search settlement lines' }),
      'refund',
    );
    expect(screen.getByRole('button', { name: 'Partial refund to review' })).toBeInTheDocument();
    expect(
      screen.queryByRole('button', {
        name: `Open ${INSUFFICIENT_DECISION.subject_settlement_line_id}`,
      }),
    ).not.toBeInTheDocument();
    await userEvent.clear(screen.getByRole('textbox'));
    await userEvent.type(screen.getByRole('textbox'), 'no-such-record');
    expect(screen.getByText('No lines match these filters.')).toBeInTheDocument();
  });
  it('reaches the ninth item and pages back without losing the denominator', async () => {
    const many: DecisionView[] = Array.from({ length: 10 }, (_, n) => ({
      ...EXCEPTION_DECISION,
      decision_id: `d${String(n)}`,
      subject_settlement_line_id: `line-${String(n).padStart(2, '0')}`,
    }));
    client.getRun.mockResolvedValue({
      run: { ...RUN, decision_count: 10, status_counts: { EXCEPTION: 10 } },
      decisions: many,
      filtered: false,
    });
    await open();
    expect(screen.getByText(/1–8 of 10 lines/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByRole('button', { name: 'Open line-08' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(screen.getByRole('button', { name: 'Open line-00' })).toBeInTheDocument();
  });
  it('keeps currencies separate and offers an explicit currency filter', async () => {
    const inr = WORKBOARD.workboard.currency_queues[0];
    if (!inr) throw new Error('Currency fixture missing');
    const base = inr.items[0];
    if (!base) throw new Error('Item fixture missing');
    client.getWorkboard.mockResolvedValue({
      ...WORKBOARD,
      workboard: {
        ...WORKBOARD.workboard,
        currency_queues: [
          inr,
          {
            currency: 'USD',
            items: [
              {
                ...base,
                decision_id: INSUFFICIENT_DECISION.decision_id,
                declared_settlement_value: {
                  ...base.declared_settlement_value,
                  currency: 'USD',
                  net_minor: 100,
                },
              },
            ],
          },
        ],
      },
    });
    await open();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Currency' }), 'USD');
    expect(screen.getByText(/USD.*1.00/)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Partial refund to review' }),
    ).not.toBeInTheDocument();
  });
  it('exports the open work and bank counts without inventing a cash-at-risk total', async () => {
    await open();
    await userEvent.click(screen.getByRole('button', { name: 'Export brief' }));
    const text = workspace.downloadText.mock.calls[0]?.[1];
    expect(text).toContain('Matched: 1');
    expect(text).toContain('Unresolved: 2');
    expect(text).toContain('Verified bank credits: 1 / 2');
    expect(text).not.toMatch(/cash at risk:/i);
  });
  it('reconciles again using the existing API', async () => {
    await open();
    await userEvent.click(screen.getByRole('button', { name: 'Reconcile latest records' }));
    expect(await screen.findByText(/Reconciliation complete/)).toBeInTheDocument();
    expect(client.createRun).toHaveBeenCalledOnce();
  });
  it('refreshes finality without changing the settlement result', async () => {
    await open();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh bank check' }));
    expect(await screen.findByText(/Bank check complete: 1 of 2/)).toBeInTheDocument();
    expect(client.createRun).not.toHaveBeenCalled();
  });
  it('never presents a newer bank audit as this batch’s finality', async () => {
    client.createBankFinalityAudit.mockResolvedValue({
      audit: { ...BANK_AUDIT, snapshot_fingerprint: 'newer' },
      created: true,
    });
    await open();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh bank check' }));
    expect(await screen.findByText(/Bank check saved for newer evidence/)).toBeInTheDocument();
  });
  it('distinguishes an unavailable bank query from zero verified credits', async () => {
    client.listBankFinalityAudits.mockRejectedValue(new Error('unavailable'));
    await open();
    expect(screen.getByText('Bank check unavailable')).toBeInTheDocument();
  });
  it('does not calculate a match rate over no decisions', async () => {
    client.getRun.mockResolvedValue({
      run: { ...RUN, decision_count: 0, status_counts: {} },
      decisions: [],
      filtered: false,
    });
    client.listBankFinalityAudits.mockResolvedValue(NO_BANK_AUDITS);
    await open();
    expect(screen.queryByText(/NaN|Infinity|100.0%/)).not.toBeInTheDocument();
    expect(screen.getByText('No lines in this view.')).toBeInTheDocument();
  });
  it('reports a mutation failure and leaves the batch visible', async () => {
    client.createRun.mockRejectedValue(new Error('network failed'));
    await open();
    await userEvent.click(screen.getByRole('button', { name: 'Reconcile latest records' }));
    await screen.findByRole('alert');
    expect(client.createRun).toHaveBeenCalledOnce();
  });
  it('reports a read failure without fabricating sample results', async () => {
    client.listRuns.mockRejectedValue(new Error('offline'));
    renderScreen(<DeskPage />);
    await screen.findByRole('alert');
    expect(screen.queryByRole('region', { name: 'Batch overview' })).not.toBeInTheDocument();
  });
});

describe('first useful result', () => {
  beforeEach(() => client.listRuns.mockResolvedValue({ runs: [], total: 0, limit: 1, offset: 0 }));
  it('imports the sample and creates a bank check from one action', async () => {
    renderScreen(<DeskPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Explore a sample business' }));
    await waitFor(() => {
      expect(client.createBankFinalityAudit).toHaveBeenCalledOnce();
    });
    expect(workspace.prepareSampleWorkspace).toHaveBeenCalledOnce();
    expect(await screen.findByText(/Your working batch is ready/)).toBeInTheDocument();
  });
  it('retains a successful reconciliation when only the bank check fails', async () => {
    client.createBankFinalityAudit.mockRejectedValue(new Error('bank unavailable'));
    renderScreen(<DeskPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Explore a sample business' }));
    expect(
      await screen.findByText(/Your batch is ready. The bank check did not finish/),
    ).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: 'Batch overview' })).toBeInTheDocument();
  });
  it('offers reconciliation for already imported records', async () => {
    renderScreen(<DeskPage />);
    await userEvent.click(await screen.findByRole('button', { name: /Already added records/ }));
    await waitFor(() => {
      expect(client.createRun).toHaveBeenCalledOnce();
    });
    expect(workspace.prepareSampleWorkspace).not.toHaveBeenCalled();
  });
});
