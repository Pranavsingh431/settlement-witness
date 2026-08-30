/** Tests for the public Track 04 landing page. */

import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/errors';
import { DEMO_BATCH } from '../test/fixtures';
import { renderScreen } from '../test/render';
import { DashboardPage } from './DashboardPage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the Track 04 landing page', () => {
  it('leads with the finance-ops loop and a 59-case batch instead of an empty dashboard', () => {
    renderScreen(<DashboardPage />);

    expect(screen.getByText(/track 04.*ai finance controller/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /close the batch/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run the 59-case batch/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /track 04 fit/i })).toHaveTextContent(/50\+/);
    expect(client.runDemoBatch).not.toHaveBeenCalled();
  });

  it('shows throughput, operational match rate and the full exception breakdown separately', async () => {
    client.runDemoBatch.mockResolvedValue(DEMO_BATCH);
    renderScreen(<DashboardPage />);

    screen.getByRole('button', { name: /run the 59-case batch/i }).click();

    const result = await screen.findByRole('region', { name: /59-case batch result/i });
    expect(result).toHaveTextContent(/59 settlement decisions from 180 synthetic source records/i);
    expect(result).toHaveTextContent(/auto-match rate/i);
    expect(result).toHaveTextContent(/54\.2%/i);
    expect(result).toHaveTextContent(/27 lines did not auto-resolve/i);
    expect(result).toHaveTextContent(/amount mismatch/i);
    expect(result).toHaveTextContent(/contract agreement: 59 \/ 59/i);
    expect(result).toHaveTextContent(/false resolutions: 0 \/ 27/i);
    expect(result).toHaveTextContent(/not a production-accuracy claim/i);
    expect(client.runDemoBatch).toHaveBeenCalledTimes(1);
  });

  it('does not hide a failed batch run behind an empty result', async () => {
    client.runDemoBatch.mockRejectedValue(new ApiError(503, 'unavailable', 'demo unavailable'));
    renderScreen(<DashboardPage />);

    screen.getByRole('button', { name: /run the 59-case batch/i }).click();

    expect(await screen.findByRole('alert')).toHaveTextContent(/demo unavailable/i);
    expect(screen.queryByRole('region', { name: /59-case batch result/i })).not.toBeInTheDocument();
  });
});
