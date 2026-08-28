/**
 * Tests for the run audit screen and the certificate it shows.
 *
 * The certificate is the part that has to be right. A passed check, a broken
 * check and a check that could not be run are three different answers, and a
 * reader has to be able to tell them apart without inspecting colours.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NetworkError } from '../api/errors';
import type { BankFinalityCertificate } from '../api/types';
import {
  AMOUNT_MISMATCH_CERTIFICATE,
  BANK_AUDIT,
  BANK_AUDITS,
  BANK_AUDIT_DETAIL,
  EXCEPTION_DECISION,
  NO_BANK_AUDITS,
  VERIFIED_CERTIFICATE,
  INSUFFICIENT_DECISION,
  RESOLVED_DECISION,
  RUN,
} from '../test/fixtures';
import { renderRoute } from '../test/render';
import { RunAuditPage } from './RunAuditPage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const ALL_DECISIONS = [EXCEPTION_DECISION, RESOLVED_DECISION, INSUFFICIENT_DECISION];

function render(): void {
  renderRoute('/runs/:runId', <RunAuditPage />, `/runs/${RUN.run_id}`);
}

/**
 * Find one line of the certificate by the text it displays.
 *
 * The line is `INV-002 · Broken`, with the identifier in its own element so it
 * can be set in a monospace face. That splits it across nodes, so the whole
 * rendered line is matched rather than either half of it.
 */
function certificateLine(pattern: RegExp): HTMLElement {
  return screen.getByText(
    (_, element) =>
      element?.classList.contains('check__title') === true && pattern.test(element.textContent),
  );
}

function noCertificateLine(pattern: RegExp): void {
  expect(
    screen.queryByText(
      (_, element) =>
        element?.classList.contains('check__title') === true && pattern.test(element.textContent),
    ),
  ).toBeNull();
}

beforeEach(() => {
  vi.resetAllMocks();
  client.getRun.mockResolvedValue({ run: RUN, decisions: ALL_DECISIONS, filtered: false });
  client.listBankFinalityAudits.mockResolvedValue(NO_BANK_AUDITS);
  client.getBankFinalityAudit.mockResolvedValue(BANK_AUDIT_DETAIL);
  client.createBankFinalityAudit.mockResolvedValue({ audit: BANK_AUDIT, created: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('run metadata', () => {
  it('reads the run id from the address', async () => {
    render();

    await waitFor(() => {
      expect(client.getRun).toHaveBeenCalledWith(RUN.run_id, expect.anything());
    });
  });

  it('shows the snapshot fingerprint in full, because an auditor compares it', async () => {
    render();

    expect(await screen.findByText(RUN.snapshot_fingerprint)).toBeInTheDocument();
  });

  it('shows every version that could change the outcome', async () => {
    render();
    await screen.findByText(RUN.snapshot_fingerprint);

    expect(screen.getByText('Baseline version').nextSibling).toHaveTextContent('1.0.0');
    expect(screen.getByText('Domain contract version').nextSibling).toHaveTextContent('5.0.0');
    expect(screen.getByText('Parser version').nextSibling).toHaveTextContent('3.1.0');
  });

  it('shows the as-of and recorded times', async () => {
    render();
    await screen.findByText(RUN.snapshot_fingerprint);

    expect(screen.getByText('As of').nextSibling).toHaveTextContent('2026-08-24 12:00:00 UTC');
  });

  it('says the counts describe the whole run, not the filtered view', async () => {
    render();

    expect(await screen.findByText(/never a filtered view/i)).toBeInTheDocument();
  });
});

describe('the decision list', () => {
  it('lists every decision with a text-labelled status', async () => {
    render();
    const table = await screen.findByRole('table', { name: /decision/i });

    expect(within(table).getByText('Resolved')).toBeInTheDocument();
    expect(within(table).getAllByText('Exception')).toHaveLength(1);
    expect(within(table).getByText('Insufficient evidence')).toBeInTheDocument();
  });

  it('shows how many citations resolved for each line', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    expect(screen.getByText('0/1')).toBeInTheDocument();
  });

  it('passes a status filter to the API', async () => {
    render();
    await screen.findByLabelText(/^status$/i);

    await userEvent.selectOptions(screen.getByLabelText(/^status$/i), 'EXCEPTION');

    await waitFor(() => {
      expect(client.getRun).toHaveBeenLastCalledWith(
        RUN.run_id,
        expect.objectContaining({ status: 'EXCEPTION' }),
      );
    });
  });

  it('offers only the exception codes this run actually raised', async () => {
    render();
    const select = await screen.findByLabelText(/exception code/i);

    const options = within(select)
      .getAllByRole('option')
      .map((one) => one.textContent);
    expect(options).toEqual(['Any exception', 'PARTIAL_REFUND', 'UNSUPPORTED_STATE']);
  });

  it('passes an exception filter to the API', async () => {
    render();
    await screen.findByLabelText(/exception code/i);

    await userEvent.selectOptions(screen.getByLabelText(/exception code/i), 'PARTIAL_REFUND');

    await waitFor(() => {
      expect(client.getRun).toHaveBeenLastCalledWith(
        RUN.run_id,
        expect.objectContaining({ exception_code: 'PARTIAL_REFUND' }),
      );
    });
  });

  it('says when the list is filtered', async () => {
    client.getRun.mockResolvedValue({
      run: RUN,
      decisions: [EXCEPTION_DECISION],
      filtered: true,
    });
    render();

    expect(await screen.findByText(/counts above still describe the whole run/i)).toBeVisible();
  });

  it('says when a filter matches nothing', async () => {
    client.getRun.mockResolvedValue({ run: RUN, decisions: [], filtered: true });
    render();

    expect(await screen.findByText(/no decision matches these filters/i)).toBeInTheDocument();
  });
});

describe('selecting a decision', () => {
  it('shows the first one without being asked', async () => {
    render();

    expect(await screen.findByText(/why this line was decided the way it was/i)).toBeVisible();
  });

  it('switches the certificate when another line is chosen', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0002/ }));

    expect(
      await screen.findByText(/every required invariant held and every citation resolved/i),
    ).toBeInTheDocument();
  });

  it('can be chosen with the keyboard', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });
    const target = screen.getByRole('button', { name: /line-0003/ });

    target.focus();
    expect(target).toHaveFocus();
    await userEvent.keyboard('{Enter}');

    expect(await screen.findByText(/could not be judged/i)).toBeInTheDocument();
  });

  it('says which line is selected, for a reader who cannot see the highlight', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0002/ }));

    expect(screen.getByRole('button', { name: /line-0002/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: /line-0001/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});

describe('the certificate', () => {
  it('separates a check that held from one that did not', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0001/ }));
    await screen.findByText(/exceptions raised/i);

    expect(certificateLine(/INV-002 · Broken/)).toBeInTheDocument();
  });

  it('marks a check that could not be run as unknown rather than failed', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0003/ }));
    await screen.findByText(/could not be judged/i);

    expect(certificateLine(/INV-003 · Could not be checked/)).toBeInTheDocument();
    noCertificateLine(/INV-003 · Broken/);
  });

  it('marks an inapplicable check differently again', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0002/ }));
    await screen.findByText(/every required invariant held/i);

    expect(certificateLine(/INV-004 · Does not apply/)).toBeInTheDocument();
  });

  it('shows expected and observed values labelled as minor units, with no currency', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0001/ }));
    await screen.findByText(/exceptions raised/i);

    expect(screen.getByText('1,000,000')).toBeInTheDocument();
    expect(screen.getByText('1,150,000')).toBeInTheDocument();
    expect(screen.getByText('minor units')).toBeInTheDocument();
    expect(screen.queryByText(/₹|\$|€|£|INR/)).not.toBeInTheDocument();
  });

  it('shows no amounts for a check that carries none', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0003/ }));
    await screen.findByText(/could not be judged/i);

    expect(screen.queryByText('minor units')).not.toBeInTheDocument();
  });

  it('says a citation resolved to a stored fact, and shows the hash in full', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0002/ }));

    expect(await screen.findByText(/verified against the stored fact/i)).toBeInTheDocument();
    const [citation] = RESOLVED_DECISION.evidence;
    expect(citation).toBeDefined();
    expect(screen.getByText(`payload hash ${citation?.payload_hash ?? ''}`)).toBeInTheDocument();
  });

  it('says a citation found nothing, without calling it a failure', async () => {
    render();
    await screen.findByRole('table', { name: /decision/i });

    await userEvent.click(screen.getByRole('button', { name: /line-0003/ }));

    expect(await screen.findByText(/no stored fact with this id/i)).toBeInTheDocument();
  });

  it('never says a hash is the document', async () => {
    render();
    const panel = await screen.findByRole('region', { name: /certificate/i });

    expect(within(panel).getByText(/the hash is not the document/i)).toBeInTheDocument();
  });

  it('offers nothing that claims to change a decision', async () => {
    render();
    const panel = await screen.findByRole('region', { name: /certificate/i });

    expect(
      within(panel).queryByRole('button', { name: /resolve|override|fix|approve/i }),
    ).toBeNull();
    expect(within(panel).getByText(/decisions are immutable/i)).toBeInTheDocument();
  });

  it('does not claim a rule was broken when an exception came from a code alone', async () => {
    const noBrokenRule = {
      ...EXCEPTION_DECISION,
      invariant_results: [
        {
          invariant_id: 'INV-001',
          outcome: 'PASSED' as const,
          reason_code: null,
          expected_minor: null,
          observed_minor: null,
        },
      ],
    };
    client.getRun.mockResolvedValue({ run: RUN, decisions: [noBrokenRule], filtered: false });
    render();

    expect(await screen.findByText(/every invariant held/i)).toBeInTheDocument();
    expect(screen.queryByText(/at least one required invariant did not hold/i)).toBeNull();
  });

  it('names the exception codes the decision raised', async () => {
    render();
    const panel = await screen.findByRole('region', { name: /certificate/i });

    expect(within(panel).getByText('PARTIAL_REFUND')).toBeInTheDocument();
  });
});

describe('when the run cannot be loaded', () => {
  it('reports it with a retry and a way back', async () => {
    client.getRun.mockRejectedValue(new NetworkError());
    render();

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be reached/i);
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to runs/i })).toHaveAttribute('href', '/runs');
  });
});

describe('reaching the review queue', () => {
  it('links to the queue for this run', async () => {
    client.getRun.mockResolvedValue({ run: RUN, decisions: ALL_DECISIONS, filtered: false });
    render();

    const link = await screen.findByRole('link', { name: /review queue/i });

    expect(link).toHaveAttribute('href', `/runs/${RUN.run_id}/review`);
  });
});

describe('bank finality', () => {
  /**
   * The panel exists to keep two conclusions apart.
   *
   * A settlement decision says the provider's own records agree. Bank finality
   * says a bank statement shows the money arriving. Only the second means the
   * merchant has been paid, and a screen that let one be read as the other
   * would be the most expensive kind of wrong this project can be.
   */
  async function auditedRun(): Promise<void> {
    client.listBankFinalityAudits.mockResolvedValue(BANK_AUDITS);
    render();
    // Waits for the certificates rather than the panel, which is on screen
    // before either request settles.
    await screen.findByRole('group', { name: /bank finality summary/i });
  }

  it('says nothing has been checked when there is no audit', async () => {
    render();

    expect(
      await screen.findByText(/no bank finality audit for this snapshot yet/i),
    ).toBeInTheDocument();
  });

  it('makes no claim about any payout without an audit', async () => {
    render();

    expect(
      await screen.findByText(/this system makes no claim that any payout reached the merchant/i),
    ).toBeInTheDocument();
  });

  it('looks the audit up by the run own snapshot fingerprint', async () => {
    render();

    await waitFor(() => {
      expect(client.listBankFinalityAudits).toHaveBeenCalledWith({
        snapshot_fingerprint: RUN.snapshot_fingerprint,
        limit: 1,
      });
    });
  });

  it('records an audit when asked', async () => {
    render();
    await userEvent.click(await screen.findByRole('button', { name: /audit bank finality/i }));

    await waitFor(() => {
      expect(client.createBankFinalityAudit).toHaveBeenCalled();
    });
  });

  it('reports a failure to record without hiding the run', async () => {
    client.createBankFinalityAudit.mockRejectedValue(new NetworkError(new Error('offline')));
    render();
    await userEvent.click(await screen.findByRole('button', { name: /audit bank finality/i }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/run metadata/i)).toBeInTheDocument();
  });

  it('says the two conclusions are separate, above the outcomes', async () => {
    await auditedRun();

    expect(
      await screen.findByText(/settlement decision and bank finality are separate conclusions/i),
    ).toBeInTheDocument();
  });

  it('counts verified credits rather than rating them', async () => {
    await auditedRun();
    const stats = await screen.findByRole('group', { name: /bank finality summary/i });

    expect(within(stats).getByText('Bank credits verified')).toBeInTheDocument();
    expect(within(stats).queryByText(/%/)).not.toBeInTheDocument();
  });

  it('shows what a verified credit was compared against', async () => {
    await auditedRun();

    expect(await screen.findByText(/bank credit verified/i)).toBeInTheDocument();
    expect(screen.getByText('Payout says')).toBeInTheDocument();
    expect(screen.getByText('Bank says')).toBeInTheDocument();
    expect(screen.getByText('BANKTXN0001')).toBeInTheDocument();
  });

  it('shows the cited records with their payload hashes', async () => {
    await auditedRun();

    expect(await screen.findByText('9c2f1a4b:PSP_API:PAYOUT:2')).toBeInTheDocument();
    expect(screen.getByText('3e7d5c1f:PSP_API:BANK_TRANSACTION:2')).toBeInTheDocument();
  });

  it('explains an unlinkable payout as a gap rather than a discrepancy', async () => {
    await auditedRun();

    expect(await screen.findByText(/no reference to match on/i)).toBeInTheDocument();
    expect(screen.getByText(/none on the payout record/i)).toBeInTheDocument();
  });

  it('never renders a finality outcome as a settlement status badge', async () => {
    await auditedRun();
    await screen.findByText(/bank credit verified/i);

    const badges = screen.getAllByText('Resolved');
    for (const badge of badges) {
      expect(badge.closest('.certificate')).toBeNull();
    }
  });

  it('never uses the word resolved inside a certificate', async () => {
    await auditedRun();
    const certificate = (await screen.findByText(/bank credit verified/i)).closest('.certificate');

    expect(certificate).not.toBeNull();
    expect(certificate).not.toHaveTextContent(/resolved/i);
  });

  it('shows a mismatch as a mismatch with both numbers', async () => {
    client.listBankFinalityAudits.mockResolvedValue(BANK_AUDITS);
    client.getBankFinalityAudit.mockResolvedValue({
      ...BANK_AUDIT_DETAIL,
      certificates: [AMOUNT_MISMATCH_CERTIFICATE],
    });
    render();

    expect(await screen.findByText(/amount differs/i)).toBeInTheDocument();
    expect(screen.getByText(/no tolerance band/i)).toBeInTheDocument();
    expect(screen.getByText(/1,220,500 INR/)).toBeInTheDocument();
    expect(screen.getByText(/1,220,501 INR CREDIT/)).toBeInTheDocument();
  });

  it('does not offer to audit again once an audit exists', async () => {
    await auditedRun();

    expect(screen.queryByRole('button', { name: /audit bank finality/i })).not.toBeInTheDocument();
  });

  it('reports a failure to read the audit list', async () => {
    client.listBankFinalityAudits.mockRejectedValue(new NetworkError(new Error('offline')));
    render();

    expect(await screen.findByText(/could not load the bank finality audit/i)).toBeInTheDocument();
  });
});

describe('a bank finality outcome this build has not heard of', () => {
  /**
   * The backend owns this vocabulary and can add to it.
   *
   * Refusing to render a whole audit because one certificate carries a code
   * this build predates would be worse than showing the code as it arrived.
   */
  it('shows the code rather than a blank', async () => {
    client.listBankFinalityAudits.mockResolvedValue(BANK_AUDITS);
    client.getBankFinalityAudit.mockResolvedValue({
      ...BANK_AUDIT_DETAIL,
      certificates: [
        {
          ...VERIFIED_CERTIFICATE,
          outcome: 'BANK_ACCOUNT_CLOSED' as BankFinalityCertificate['outcome'],
        },
      ],
    });
    render();

    const codes = await screen.findAllByText('BANK_ACCOUNT_CLOSED');
    expect(codes.length).toBeGreaterThan(0);
  });
});
