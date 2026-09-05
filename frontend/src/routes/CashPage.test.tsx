import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CashPage } from './CashPage';
import { renderScreen } from '../test/render';
import {
  BANK_AUDITS,
  NO_BANK_AUDITS,
  BANK_AUDIT,
  BANK_AUDIT_DETAIL,
  VERIFIED_CERTIFICATE,
  UNLINKABLE_CERTIFICATE,
} from '../test/fixtures';
vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));
beforeEach(() => {
  vi.resetAllMocks();
  client.listBankFinalityAudits.mockResolvedValue(BANK_AUDITS);
  client.getBankFinalityAudit.mockResolvedValue(BANK_AUDIT_DETAIL);
  client.createBankFinalityAudit.mockResolvedValue({ audit: BANK_AUDIT, created: true });
});
describe('bank credits workspace', () => {
  it('shows actual payout evidence and inspects the selected certificate', async () => {
    renderScreen(<CashPage />);
    await userEvent.click(
      await screen.findByRole('button', { name: VERIFIED_CERTIFICATE.payout_id }),
    );
    expect(screen.getByRole('complementary', { name: 'Bank evidence' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close bank evidence' }));
    expect(screen.queryByRole('complementary', { name: 'Bank evidence' })).not.toBeInTheDocument();
  });
  it('filters verified and unverified payouts without changing summary totals', async () => {
    renderScreen(<CashPage />);
    await screen.findByRole('region', { name: 'Bank credit summary' });
    await userEvent.click(screen.getByRole('button', { name: 'Needs follow-up' }));
    expect(
      screen.queryByRole('button', { name: VERIFIED_CERTIFICATE.payout_id }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: UNLINKABLE_CERTIFICATE.payout_id }),
    ).toBeInTheDocument();
    expect(screen.getByText('Reference missing')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Verified credits' }));
    expect(
      screen.getByRole('button', { name: VERIFIED_CERTIFICATE.payout_id }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'All payouts' }));
    expect(
      screen.getByRole('button', { name: UNLINKABLE_CERTIFICATE.payout_id }),
    ).toBeInTheDocument();
  });
  it('records a new check and reloads the results', async () => {
    renderScreen(<CashPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Check bank credits' }));
    await waitFor(() => {
      expect(client.listBankFinalityAudits).toHaveBeenCalledTimes(2);
    });
    expect(client.createRun).not.toHaveBeenCalled();
  });
  it('shows an empty workspace without inventing a check', async () => {
    client.listBankFinalityAudits.mockResolvedValue(NO_BANK_AUDITS);
    renderScreen(<CashPage />);
    expect(await screen.findByRole('link', { name: /Add bank records/ })).toHaveAttribute(
      'href',
      '/imports',
    );
    expect(client.getBankFinalityAudit).not.toHaveBeenCalled();
  });
  it('reports a read error instead of an empty result', async () => {
    client.listBankFinalityAudits.mockRejectedValue(new Error('offline'));
    renderScreen(<CashPage />);
    await screen.findByRole('alert');
    expect(screen.queryByRole('link', { name: /Add bank records/ })).not.toBeInTheDocument();
  });
  it('reports a failed check without claiming any payout verified', async () => {
    client.createBankFinalityAudit.mockRejectedValue(new Error('offline'));
    renderScreen(<CashPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Check bank credits' }));
    await screen.findByRole('alert');
    expect(client.createBankFinalityAudit).toHaveBeenCalledOnce();
  });
  it('distinguishes no rows in a filtered view from missing amount evidence', async () => {
    client.getBankFinalityAudit.mockResolvedValue({
      ...BANK_AUDIT_DETAIL,
      certificates: [
        { ...UNLINKABLE_CERTIFICATE, expected_amount_minor: null, expected_currency: null },
      ],
    });
    renderScreen(<CashPage />);
    await screen.findByRole('button', { name: UNLINKABLE_CERTIFICATE.payout_id });
    expect(screen.getByText('—')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Verified credits' }));
    expect(screen.getByText('No payouts in this view.')).toBeInTheDocument();
  });
});
