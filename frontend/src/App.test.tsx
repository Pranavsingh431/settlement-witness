/**
 * Tests for the shell: navigation, routing, and the way in for a keyboard user.
 */

import { screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { renderScreen } from './test/render';
import { App } from './App';

vi.mock('./api/client');
const client = vi.mocked(await import('./api/client'));

beforeEach(() => {
  vi.resetAllMocks();
  client.listBankFinalityAudits.mockResolvedValue({
    audits: [],
    total: 0,
    limit: 1,
    offset: 0,
    filtered: true,
    bank_finality_version: '1.0.0',
    settlement_and_finality_are_separate: 'separate conclusions',
  });
  client.listRuns.mockResolvedValue({ runs: [], total: 0, limit: 1, offset: 0 });
  client.listImports.mockResolvedValue({
    receipts: [],
    total: 0,
    limit: 5,
    offset: 0,
    filtered: false,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the shell', () => {
  it('names the product and its claim', () => {
    renderScreen(<App />);

    expect(screen.getByText('Settlement Witness')).toBeInTheDocument();
    expect(
      within(screen.getByRole('banner')).getByText(/payout controls, made auditable/i),
    ).toBeInTheDocument();
  });

  it('offers a skip link before the navigation', () => {
    renderScreen(<App />);

    const skip = screen.getByRole('link', { name: /skip to content/i });
    expect(skip).toHaveAttribute('href', '#main');
    expect(document.querySelector('main')).toHaveAttribute('id', 'main');
  });

  it('marks the section the reader is in', () => {
    renderScreen(<App />, '/imports');

    expect(screen.getByRole('link', { name: 'Evidence' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Audits' })).not.toHaveAttribute('aria-current');
  });

  it('puts the navigation in a named landmark', () => {
    renderScreen(<App />);

    expect(screen.getByRole('navigation', { name: /sections/i })).toBeInTheDocument();
  });
});

describe('routing', () => {
  it('shows the overview at the root', async () => {
    renderScreen(<App />, '/');

    expect(
      await screen.findByRole('heading', { name: /know which settlements you can stand behind/i }),
    ).toBeInTheDocument();
  });

  it('shows the import screen', async () => {
    renderScreen(<App />, '/imports');

    expect(await screen.findByRole('heading', { name: /import evidence/i })).toBeInTheDocument();
  });

  it('shows the runs screen', async () => {
    renderScreen(<App />, '/runs');

    expect(
      await screen.findByRole('heading', { name: /reconciliation runs/i }),
    ).toBeInTheDocument();
  });

  it('shows the audit screen for one run', async () => {
    client.getRun.mockResolvedValue({
      run: {
        run_id: 'r1',
        snapshot_fingerprint: 'f',
        baseline_version: '1.0.0',
        domain_schema_version: '5.0.0',
        parser_version: '3.1.0',
        created_at: '2026-08-24T12:00:00Z',
        as_of: '2026-08-24T12:00:00Z',
        fact_count: 0,
        settlement_line_count: 0,
        decision_count: 0,
        status_counts: {},
        exception_counts: {},
      },
      decisions: [],
      filtered: false,
    });
    renderScreen(<App />, '/runs/r1');

    expect(await screen.findByRole('heading', { name: /run audit/i })).toBeInTheDocument();
  });

  it('says plainly when an address is not part of the application', () => {
    renderScreen(<App />, '/nowhere');

    expect(screen.getByText(/no such page/i)).toBeInTheDocument();
  });
});
