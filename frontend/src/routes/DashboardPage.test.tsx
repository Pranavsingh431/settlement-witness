/**
 * Tests for the overview screen.
 *
 * The empty state gets the most attention here. An empty store showing zeroes
 * in the same layout as real counts would read as a clean bill of health, which
 * is the exact false reassurance this project exists to avoid.
 */

import { screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/errors';
import { ACCEPTED_RECEIPT, INVALID_RECEIPT, RUN } from '../test/fixtures';
import { renderScreen } from '../test/render';
import { DashboardPage } from './DashboardPage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const NO_RUNS = { runs: [], total: 0, limit: 1, offset: 0 };
const NO_IMPORTS = { receipts: [], total: 0, limit: 5, offset: 0, filtered: false };

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('an empty store', () => {
  beforeEach(() => {
    client.listRuns.mockResolvedValue(NO_RUNS);
    client.listImports.mockResolvedValue(NO_IMPORTS);
  });

  it('says no run has been recorded rather than showing a run of zeroes', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/no run has been recorded yet/i)).toBeInTheDocument();
  });

  it('shows no statistics at all, so nothing reads as a completed reconciliation', async () => {
    renderScreen(<DashboardPage />);
    await screen.findByText(/no run has been recorded yet/i);

    expect(screen.queryByRole('group', { name: /run summary/i })).not.toBeInTheDocument();
  });

  it('points at importing evidence as the next step', async () => {
    renderScreen(<DashboardPage />);

    const links = await screen.findAllByRole('link', { name: /import/i });
    expect(links[0]).toHaveAttribute('href', '/imports');
  });

  it('says nothing has been imported', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/nothing has been imported/i)).toBeInTheDocument();
  });

  it('still explains the three answers, because that is what the product is', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/the three answers a line can get/i)).toBeInTheDocument();
    expect(screen.getByText(/only state that says a line is supported/i)).toBeInTheDocument();
  });

  it('offers no accuracy figure and no percentage anywhere', async () => {
    const { container } = renderScreen(<DashboardPage />);
    await screen.findByText(/no run has been recorded yet/i);

    expect(container.textContent).not.toMatch(/accuracy/i);
    expect(container.textContent).not.toMatch(/\d\s*%/);
  });
});

describe('a store with a run', () => {
  beforeEach(() => {
    client.listRuns.mockResolvedValue({ runs: [RUN], total: 1, limit: 1, offset: 0 });
    client.listImports.mockResolvedValue({
      receipts: [ACCEPTED_RECEIPT, INVALID_RECEIPT],
      total: 2,
      limit: 5,
      offset: 0,
      filtered: false,
    });
  });

  it('reports the counts the run actually recorded', async () => {
    renderScreen(<DashboardPage />);
    const summary = await screen.findByRole('group', { name: /run summary/i });

    expect(within(summary).getByText('Source facts').previousSibling).toHaveTextContent('10');
    expect(within(summary).getByText('Resolved').previousSibling).toHaveTextContent('1');
    expect(within(summary).getByText('Exceptions').previousSibling).toHaveTextContent('2');
  });

  it('shows insufficient evidence as its own number, not folded into exceptions', async () => {
    renderScreen(<DashboardPage />);
    const summary = await screen.findByRole('group', { name: /run summary/i });

    expect(within(summary).getByText('Insufficient evidence').previousSibling).toHaveTextContent(
      '0',
    );
  });

  it('names the rule versions the run used', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/baseline 1\.0\.0/)).toBeInTheDocument();
    expect(screen.getByText(/parser 3\.0\.0/)).toBeInTheDocument();
  });

  it('links to the audit for that run', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByRole('link', { name: /audit this run/i })).toHaveAttribute(
      'href',
      `/runs/${RUN.run_id}`,
    );
  });

  it('lists recent attempts including the refused one', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText('payment_events.csv')).toBeInTheDocument();
    expect(screen.getByText('invalid_mixed_rows.csv')).toBeInTheDocument();
    expect(screen.getByText(/rejected, unreadable/i)).toBeInTheDocument();
  });
});

describe('when the backend is unreachable', () => {
  it('says so and offers a retry', async () => {
    client.listRuns.mockRejectedValue(new NetworkError());
    client.listImports.mockRejectedValue(new NetworkError());

    renderScreen(<DashboardPage />);

    expect(await screen.findAllByRole('alert')).toHaveLength(2);
    expect(screen.getAllByText(/could not be reached/i)[0]).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /try again/i })[0]).toBeInTheDocument();
  });

  it('asks again when retried', async () => {
    client.listRuns.mockRejectedValue(new NetworkError());
    client.listImports.mockRejectedValue(new NetworkError());
    renderScreen(<DashboardPage />);
    const retry = (await screen.findAllByRole('button', { name: /try again/i }))[0];

    client.listRuns.mockResolvedValue(NO_RUNS);
    retry?.click();

    await waitFor(() => {
      expect(client.listRuns).toHaveBeenCalledTimes(2);
    });
  });

  it('shows the backend message when the API refuses rather than a generic one', async () => {
    client.listRuns.mockRejectedValue(new ApiError(500, 'boom', 'the store is on fire'));
    client.listImports.mockResolvedValue(NO_IMPORTS);

    renderScreen(<DashboardPage />);

    expect(await screen.findByText('the store is on fire')).toBeInTheDocument();
  });
});
