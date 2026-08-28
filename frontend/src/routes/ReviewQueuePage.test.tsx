/**
 * Tests for the review queue screen.
 *
 * The tests that matter most are the ones about what the screen never says. A
 * reviewer who believes closing an item settles a line will close items
 * believing they are settled, so the copy asserting otherwise is pinned as
 * tightly as the behaviour.
 */

import { screen, waitFor, within } from '@testing-library/react';
import type { RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/errors';
import {
  BASELINE_NOTE,
  CLOSED_ITEM,
  EMPTY_REVIEW_QUEUE,
  OPEN_ITEM,
  REVIEW_QUEUE,
  RUN,
  UNKNOWN_ITEM,
} from '../test/fixtures';
import { renderRoute } from '../test/render';
import { ReviewQueuePage } from './ReviewQueuePage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const AT = `/runs/${RUN.run_id}/review`;
const PATTERN = '/runs/:runId/review';

function open(): RenderResult {
  return renderRoute(PATTERN, <ReviewQueuePage />, AT);
}

beforeEach(() => {
  vi.resetAllMocks();
  client.getReviewQueue.mockResolvedValue(REVIEW_QUEUE);
  client.appendReviewEvent.mockResolvedValue({
    event: {
      event_id: 'new-event',
      sequence: 1,
      action: 'ACKNOWLEDGED',
      note: null,
      recorded_at: '2026-08-27T11:00:00Z',
      decision_fingerprint: OPEN_ITEM.decision_fingerprint,
    },
    workflow_state: 'ACKNOWLEDGED',
    baseline_status: 'EXCEPTION',
    baseline_unchanged_note: BASELINE_NOTE,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the queue', () => {
  it('lists the lines the run did not resolve', async () => {
    open();

    expect(await screen.findByRole('button', { name: 'line-0001' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'line-0003' })).toBeInTheDocument();
  });

  it('shows the baseline status and the workflow state as two different things', async () => {
    open();
    const row = (await screen.findByRole('button', { name: 'line-0003' })).closest('tr');

    expect(within(row as HTMLElement).getByText('Insufficient evidence')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Waiting for evidence')).toBeInTheDocument();
  });

  it('counts what needs review, what is open, and what is closed', async () => {
    open();
    const stats = await screen.findByRole('group', { name: /queue summary/i });

    expect(within(stats).getByText('Needing review')).toBeInTheDocument();
    expect(within(stats).getByText('Still open')).toBeInTheDocument();
    expect(within(stats).getByText('Closed without override')).toBeInTheDocument();
  });

  it('says the workflow state does not change the decision, before anything is clicked', async () => {
    open();

    const notes = await screen.findAllByText(/human workflow state does not change the baseline/i);
    expect(notes.length).toBeGreaterThan(0);
  });

  it('reports an empty queue as a result rather than a missing screen', async () => {
    client.getReviewQueue.mockResolvedValue(EMPTY_REVIEW_QUEUE);
    open();

    expect(await screen.findByText(/nothing in this run needs a person/i)).toBeInTheDocument();
    expect(screen.getByText(/an empty queue is a result/i)).toBeInTheDocument();
  });

  it('shows a loading state while the queue is being read', () => {
    client.getReviewQueue.mockReturnValue(new Promise(() => undefined));
    open();

    expect(screen.getByRole('status')).toHaveTextContent(/loading the review queue/i);
  });

  it('reports a failure with the backend words and a way to try again', async () => {
    client.getReviewQueue.mockRejectedValue(new NetworkError(new Error('offline')));
    open();

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('retries when asked', async () => {
    client.getReviewQueue.mockRejectedValueOnce(new NetworkError(new Error('offline')));
    client.getReviewQueue.mockResolvedValue(REVIEW_QUEUE);
    open();

    await userEvent.click(await screen.findByRole('button', { name: /try again/i }));

    expect(await screen.findByRole('button', { name: 'line-0001' })).toBeInTheDocument();
  });
});

describe('the workspace', () => {
  it('shows the certificate of the selected line', async () => {
    open();

    expect(await screen.findByText(/invariant certificate/i)).toBeInTheDocument();
    expect(screen.getByText(/cited evidence/i)).toBeInTheDocument();
  });

  it('shows an empty timeline as an explanation, not a blank', async () => {
    open();

    expect(
      await screen.findByText(/nothing has been recorded against this line yet/i),
    ).toBeInTheDocument();
  });

  it('shows a recorded note as text', async () => {
    open();
    await userEvent.click(await screen.findByRole('button', { name: 'line-0003' }));

    expect(await screen.findByText('need the 3 March bank statement')).toBeInTheDocument();
  });

  it('renders a note containing markup as characters, not as markup', async () => {
    client.getReviewQueue.mockResolvedValue({
      ...REVIEW_QUEUE,
      items: [
        {
          ...OPEN_ITEM,
          events: [
            {
              event_id: 'markup',
              sequence: 1,
              action: 'ESCALATED' as const,
              note: '<b>see</b> ticket #4',
              recorded_at: '2026-08-27T09:15:00Z',
              decision_fingerprint: OPEN_ITEM.decision_fingerprint,
            },
          ],
        },
      ],
      total: 1,
    });
    const view = open();

    expect(await screen.findByText('<b>see</b> ticket #4')).toBeInTheDocument();
    expect(view.container.querySelector('b')).toBeNull();
  });

  it('says no reviewer was recorded, because there is no sign-in', async () => {
    open();
    await userEvent.click(await screen.findByRole('button', { name: 'line-0003' }));

    expect(
      await screen.findByText(/no reviewer recorded, this system has no sign-in/i),
    ).toBeInTheDocument();
  });

  it('switches the workspace when another line is chosen', async () => {
    open();
    await userEvent.click(await screen.findByRole('button', { name: 'line-0003' }));

    const header = await screen.findByRole('group', { name: /decision and workflow/i });
    expect(header).toHaveTextContent(UNKNOWN_ITEM.decision.subject_settlement_line_id);
  });

  it('keeps the selected row addressable from the keyboard', async () => {
    open();
    const row = await screen.findByRole('button', { name: 'line-0003' });
    row.focus();
    await userEvent.keyboard('{Enter}');

    expect(row).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('recording an action', () => {
  it('offers exactly the four permitted actions', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0001' });

    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(4);
    expect(screen.getByRole('radio', { name: /acknowledge/i })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /request evidence/i })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /escalate/i })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /close without override/i })).toBeInTheDocument();
  });

  it('offers no approve, resolve or override action', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0001' });

    for (const forbidden of [/^approve/i, /^resolve/i, /^override/i, /mark as settled/i]) {
      expect(screen.queryByRole('radio', { name: forbidden })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: forbidden })).not.toBeInTheDocument();
    }
  });

  it('sends the action, the fingerprint and an idempotency key', async () => {
    open();
    await userEvent.click(await screen.findByRole('radio', { name: /escalate/i }));
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledWith(
        RUN.run_id,
        OPEN_ITEM.decision.decision_id,
        expect.objectContaining({
          action: 'ESCALATED',
          decisionFingerprint: OPEN_ITEM.decision_fingerprint,
          idempotencyKey: expect.any(String) as unknown as string,
        }),
      );
    });
  });

  it('sends a fresh idempotency key for each submission', async () => {
    open();
    await userEvent.click(await screen.findByRole('button', { name: /record this action/i }));
    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(1);
    });
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));
    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(2);
    });

    const keys = client.appendReviewEvent.mock.calls.map((call) => call[2].idempotencyKey);
    expect(new Set(keys).size).toBe(2);
  });

  it('sends the note when one was written', async () => {
    open();
    await userEvent.type(await screen.findByLabelText(/note \(optional\)/i), 'chasing the bank');
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledWith(
        RUN.run_id,
        OPEN_ITEM.decision.decision_id,
        expect.objectContaining({ note: 'chasing the bank' }),
      );
    });
  });

  it('confirms what was recorded and repeats the unchanged status', async () => {
    open();
    await userEvent.click(await screen.findByRole('button', { name: /record this action/i }));

    const confirmation = await screen.findByRole('status');
    expect(confirmation).toHaveTextContent(/the baseline decision is still/i);
    expect(confirmation).toHaveTextContent('EXCEPTION');
  });

  it('reloads the queue so the new event appears in the timeline', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0001' });
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.getReviewQueue.mock.calls.length).toBeGreaterThan(1);
    });
  });

  it('reports a refusal in the backend words without clearing the screen', async () => {
    client.appendReviewEvent.mockRejectedValue(
      new ApiError(
        409,
        'idempotency_conflict',
        'this idempotency key was used for a different review command',
      ),
    );
    open();
    await userEvent.click(await screen.findByRole('button', { name: /record this action/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /used for a different review command/i,
    );
    expect(screen.getByRole('button', { name: 'line-0001' })).toBeInTheDocument();
  });

  it('does not disable the form after a refusal', async () => {
    client.appendReviewEvent.mockRejectedValue(new NetworkError(new Error('offline')));
    open();
    await userEvent.click(await screen.findByRole('button', { name: /record this action/i }));

    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: /record this action/i })).toBeEnabled();
  });
});

describe('a closed review', () => {
  beforeEach(() => {
    client.getReviewQueue.mockResolvedValue({
      ...REVIEW_QUEUE,
      items: [CLOSED_ITEM],
      total: 1,
      open_total: 0,
    });
  });

  it('still shows the original exception status prominently', async () => {
    open();
    const header = await screen.findByRole('group', { name: /decision and workflow/i });

    expect(header).toHaveTextContent('Exception');
    expect(header).toHaveTextContent('Closed without override');
  });

  it('does not describe the line as resolved anywhere on the screen', async () => {
    open();
    await screen.findByRole('group', { name: /decision and workflow/i });

    expect(screen.queryByText(/^resolved$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/this line is settled/i)).not.toBeInTheDocument();
  });

  it('shows the closed workflow state as a workflow state, not a status', async () => {
    open();

    expect(await screen.findAllByText('Closed without override')).not.toHaveLength(0);
  });

  it('still says the workflow state does not change the decision', async () => {
    open();

    const notes = await screen.findAllByText(/human workflow state does not change the baseline/i);
    expect(notes.length).toBeGreaterThan(0);
  });
});
