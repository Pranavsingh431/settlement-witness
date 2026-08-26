/**
 * Tests for the runs screen.
 *
 * Two answers matter and must not be run together: a new run was recorded, and
 * an identical snapshot already had one. Reporting both as "done" would hide
 * the idempotency that the run key exists to provide.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/errors';
import { RUN } from '../test/fixtures';
import { renderScreen } from '../test/render';
import { RunsPage } from './RunsPage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const NO_RUNS = { runs: [], total: 0, limit: 20, offset: 0 };
const ONE_RUN = { runs: [RUN], total: 1, limit: 20, offset: 0 };

beforeEach(() => {
  vi.resetAllMocks();
  client.listRuns.mockResolvedValue(NO_RUNS);
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function reconcile(): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name: /reconcile stored facts/i }));
}

describe('creating a run', () => {
  it('reports a 201 as a new immutable run', async () => {
    client.createRun.mockResolvedValue({ run: RUN, created: true });
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByText(/a new immutable run was recorded/i)).toBeInTheDocument();
  });

  it('reports a 200 as a run that already existed, not as a new one', async () => {
    client.createRun.mockResolvedValue({ run: RUN, created: false });
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByText(/this snapshot already had a run/i)).toBeInTheDocument();
    expect(screen.queryByText(/a new immutable run was recorded/i)).not.toBeInTheDocument();
  });

  it('explains why a repeat did not write a duplicate', async () => {
    client.createRun.mockResolvedValue({ run: RUN, created: false });
    renderScreen(<RunsPage />);

    await reconcile();

    expect(
      await screen.findByText(/existing run was returned rather than a duplicate written/i),
    ).toBeInTheDocument();
  });

  it('links to the audit for whichever run came back', async () => {
    client.createRun.mockResolvedValue({ run: RUN, created: true });
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByRole('link', { name: /audit run/i })).toHaveAttribute(
      'href',
      `/runs/${RUN.run_id}`,
    );
  });

  it('refuses a second click while one is in flight', async () => {
    client.createRun.mockReturnValue(
      new Promise(() => {
        // Never settles, so the request stays in flight.
      }),
    );
    renderScreen(<RunsPage />);

    await reconcile();

    const button = screen.getByRole('button', { name: /reconciling/i });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(client.createRun).toHaveBeenCalledTimes(1);
  });

  it('reloads the run list afterwards', async () => {
    client.createRun.mockResolvedValue({ run: RUN, created: true });
    renderScreen(<RunsPage />);
    await screen.findByRole('button', { name: /reconcile stored facts/i });
    const before = client.listRuns.mock.calls.length;

    await reconcile();

    await waitFor(() => {
      expect(client.listRuns.mock.calls.length).toBeGreaterThan(before);
    });
  });
});

describe('when there is nothing to reconcile', () => {
  beforeEach(() => {
    client.createRun.mockRejectedValue(
      new ApiError(409, 'no_facts', 'the store holds no accepted source facts to reconcile'),
    );
  });

  it('says so rather than showing a run of zeroes', async () => {
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByText(/there is nothing to reconcile/i)).toBeInTheDocument();
  });

  it('sends the person to import evidence first', async () => {
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByRole('link', { name: /import evidence first/i })).toHaveAttribute(
      'href',
      '/imports',
    );
  });

  it('does not present it as a crash', async () => {
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByRole('alert')).not.toHaveTextContent(/something went wrong/i);
  });
});

describe('when the run cannot be created for another reason', () => {
  it('shows the backend message', async () => {
    client.createRun.mockRejectedValue(new ApiError(500, 'boom', 'the database is unavailable'));
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByText('the database is unavailable')).toBeInTheDocument();
  });

  it('shows an unreachable backend as its own problem', async () => {
    client.createRun.mockRejectedValue(new NetworkError());
    renderScreen(<RunsPage />);

    await reconcile();

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be reached/i);
  });
});

describe('the run list', () => {
  it('says when no run has been recorded and points at importing', async () => {
    renderScreen(<RunsPage />);

    expect(await screen.findByText(/no runs recorded/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /import evidence/i })).toHaveAttribute(
      'href',
      '/imports',
    );
  });

  it('shows the status counts and the rule versions for each run', async () => {
    client.listRuns.mockResolvedValue(ONE_RUN);
    renderScreen(<RunsPage />);

    expect(await screen.findByText(/baseline 1\.0\.0/)).toBeInTheDocument();
    expect(screen.getByText(/contract 5\.0\.0/)).toBeInTheDocument();
  });

  it('links each run to its audit', async () => {
    client.listRuns.mockResolvedValue(ONE_RUN);
    renderScreen(<RunsPage />);

    const link = await screen.findByRole('link', { name: /fd0c9443bb/ });
    expect(link).toHaveAttribute('href', `/runs/${RUN.run_id}`);
  });

  it('shows no percentage or accuracy figure', async () => {
    client.listRuns.mockResolvedValue(ONE_RUN);
    const { container } = renderScreen(<RunsPage />);
    await screen.findByText(/baseline 1\.0\.0/);

    expect(container.textContent).not.toMatch(/accuracy/i);
    expect(container.textContent).not.toMatch(/\d\s*%/);
  });

  it('reports a failure to load the list with a retry', async () => {
    client.listRuns.mockRejectedValue(new NetworkError());
    renderScreen(<RunsPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be reached/i);
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});
