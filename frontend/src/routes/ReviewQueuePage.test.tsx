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
import type { ReviewQueuePage as QueuePayload } from '../api/types';
import { ReviewQueuePage } from './ReviewQueuePage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const AT = `/runs/${RUN.run_id}/review`;
/** The page size the screen asks for. Mirrors the constant in the screen. */
const PAGE_SIZE = 20;
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

  it('orders the timeline by sequence, whatever order it arrived in', async () => {
    const at = '2026-08-27T09:15:00Z';
    client.getReviewQueue.mockResolvedValue({
      ...REVIEW_QUEUE,
      items: [
        {
          ...OPEN_ITEM,
          events: [
            {
              event_id: 'second',
              sequence: 2,
              action: 'ESCALATED' as const,
              note: 'passed to the bank team',
              recorded_at: at,
              decision_fingerprint: OPEN_ITEM.decision_fingerprint,
            },
            {
              event_id: 'first',
              sequence: 1,
              action: 'ACKNOWLEDGED' as const,
              note: 'picked this up',
              recorded_at: at,
              decision_fingerprint: OPEN_ITEM.decision_fingerprint,
            },
          ],
        },
      ],
      total: 1,
    });
    open();

    const entries = await screen.findAllByRole('listitem');
    expect(entries[0]).toHaveTextContent('picked this up');
    expect(entries[1]).toHaveTextContent('passed to the bank team');
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

  it('sends a fresh idempotency key for a deliberate second action', async () => {
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
    expect(screen.getByRole('button', { name: /retry this same action/i })).toBeEnabled();
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

describe('retrying a submission whose outcome is unknown', () => {
  /**
   * The case this whole block exists for.
   *
   * A request can fail after the server has written the row: the answer is lost
   * on the way back. The reviewer sees an error and presses the button again.
   * If that sent a new key, the server would append a second event for one
   * intended action, and nothing on either side would say so.
   */
  async function submitAndFail(): Promise<void> {
    client.appendReviewEvent.mockRejectedValueOnce(new NetworkError(new Error('answer lost')));
    open();
    await userEvent.click(await screen.findByRole('button', { name: /record this action/i }));
    await screen.findByRole('alert');
  }

  function keysSent(): string[] {
    return client.appendReviewEvent.mock.calls.map((call) => call[2].idempotencyKey);
  }

  it('reuses the key when unchanged input is retried', async () => {
    await submitAndFail();

    await userEvent.click(screen.getByRole('button', { name: /retry this same action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(2);
    });
    const [first, second] = keysSent();
    expect(second).toBe(first);
  });

  it('sends an identical command payload on the retry', async () => {
    await submitAndFail();

    await userEvent.click(screen.getByRole('button', { name: /retry this same action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(2);
    });
    const [first, second] = client.appendReviewEvent.mock.calls;
    expect(second).toEqual(first);
  });

  it('labels the retry as the same action rather than a second one', async () => {
    await submitAndFail();

    expect(screen.getByRole('button', { name: /retry this same action/i })).toBeInTheDocument();
    expect(screen.getByText(/this retries the same action, not a second one/i)).toBeInTheDocument();
  });

  it('keeps the baseline-unchanged notice while retrying', async () => {
    await submitAndFail();

    const notes = screen.getAllByText(/human workflow state does not change the baseline/i);
    expect(notes.length).toBeGreaterThan(0);
  });

  it('stops being a retry when the action is changed', async () => {
    await submitAndFail();

    await userEvent.click(screen.getByRole('radio', { name: /escalate/i }));

    expect(
      screen.queryByRole('button', { name: /retry this same action/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /record this action/i })).toBeInTheDocument();
  });

  it('sends a new key when the action is changed after a failure', async () => {
    await submitAndFail();

    await userEvent.click(screen.getByRole('radio', { name: /escalate/i }));
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(2);
    });
    const [first, second] = keysSent();
    expect(second).not.toBe(first);
  });

  it('sends a new key when the note is changed after a failure', async () => {
    await submitAndFail();

    await userEvent.type(screen.getByLabelText(/note \(optional\)/i), 'chasing the bank');
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(2);
    });
    const [first, second] = keysSent();
    expect(second).not.toBe(first);
  });

  it('treats a note that differs only by surrounding space as the same command', async () => {
    client.appendReviewEvent.mockRejectedValueOnce(new NetworkError(new Error('answer lost')));
    open();
    await userEvent.type(await screen.findByLabelText(/note \(optional\)/i), 'chasing');
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));
    await screen.findByRole('alert');

    await userEvent.type(screen.getByLabelText(/note \(optional\)/i), '  ');
    await userEvent.click(screen.getByRole('button', { name: /retry this same action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(2);
    });
    const [first, second] = keysSent();
    expect(second).toBe(first);
  });

  it('sends a new key for the next action once one is confirmed', async () => {
    await submitAndFail();

    await userEvent.click(screen.getByRole('button', { name: /retry this same action/i }));
    await screen.findByRole('status');
    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.appendReviewEvent).toHaveBeenCalledTimes(3);
    });
    const [first, second, third] = keysSent();
    expect(second).toBe(first);
    expect(third).not.toBe(first);
  });
});

describe('paging through a long queue', () => {
  const TOTAL = 21;

  function page(offset: number): QueuePayload {
    const size = Math.min(PAGE_SIZE, TOTAL - offset);
    return {
      ...REVIEW_QUEUE,
      items: Array.from({ length: size }, (_unused, index) => ({
        ...OPEN_ITEM,
        decision: {
          ...OPEN_ITEM.decision,
          decision_id: `d-${String(offset + index)}`,
          subject_settlement_line_id: `line-${String(offset + index).padStart(4, '0')}`,
        },
      })),
      total: TOTAL,
      open_total: TOTAL,
      limit: PAGE_SIZE,
      offset,
    };
  }

  beforeEach(() => {
    client.getReviewQueue.mockImplementation((_runId, filters) =>
      Promise.resolve(page(filters?.offset ?? 0)),
    );
  });

  async function next(): Promise<void> {
    await userEvent.click(screen.getByRole('button', { name: /next page/i }));
  }

  it('asks for the first page with an explicit offset', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });

    expect(client.getReviewQueue).toHaveBeenCalledWith(RUN.run_id, { limit: 20, offset: 0 });
  });

  it('asks for offset twenty when the next page is chosen', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });

    await next();

    await waitFor(() => {
      expect(client.getReviewQueue).toHaveBeenCalledWith(RUN.run_id, { limit: 20, offset: 20 });
    });
  });

  it('reaches the twenty-first item, which the first page hides', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });

    await next();

    expect(await screen.findByRole('button', { name: 'line-0020' })).toBeInTheDocument();
  });

  it('returns to offset zero on previous', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });
    await next();
    await screen.findByRole('button', { name: 'line-0020' });

    await userEvent.click(screen.getByRole('button', { name: /previous page/i }));

    expect(await screen.findByRole('button', { name: 'line-0000' })).toBeInTheDocument();
  });

  it('disables previous on the first page', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });

    expect(screen.getByRole('button', { name: /previous page/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /next page/i })).toBeEnabled();
  });

  it('disables next on the last page', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });
    await next();
    await screen.findByRole('button', { name: 'line-0020' });

    expect(screen.getByRole('button', { name: /next page/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /previous page/i })).toBeEnabled();
  });

  it('says which items are on screen and how many there are in total', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });

    expect(screen.getByText(/items 1 to 20 of 21/i)).toBeInTheDocument();

    await next();
    await screen.findByRole('button', { name: 'line-0020' });

    expect(screen.getByText(/items 21 to 21 of 21/i)).toBeInTheDocument();
  });

  it('offers the pager under a named landmark', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });

    expect(screen.getByRole('navigation', { name: /review queue pages/i })).toBeInTheDocument();
  });

  it('selects the first item of a page the previous selection is not on', async () => {
    open();
    await userEvent.click(await screen.findByRole('button', { name: 'line-0005' }));
    expect(screen.getByRole('button', { name: 'line-0005' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await next();
    await screen.findByRole('button', { name: 'line-0020' });

    expect(screen.getByRole('button', { name: 'line-0020' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('keeps the baseline status and the workflow state apart on every page', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });
    await next();
    const row = (await screen.findByRole('button', { name: 'line-0020' })).closest('tr');

    expect(within(row as HTMLElement).getByText('Exception')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Not yet picked up')).toBeInTheDocument();
  });

  it('stays on the same page after an action is recorded', async () => {
    open();
    await screen.findByRole('button', { name: 'line-0000' });
    await next();
    await screen.findByRole('button', { name: 'line-0020' });

    await userEvent.click(screen.getByRole('button', { name: /record this action/i }));

    await waitFor(() => {
      expect(client.getReviewQueue).toHaveBeenLastCalledWith(RUN.run_id, {
        limit: 20,
        offset: 20,
      });
    });
    expect(screen.getByRole('button', { name: 'line-0020' })).toBeInTheDocument();
  });

  it('offers no pager on a queue that fits one page', async () => {
    client.getReviewQueue.mockResolvedValue(REVIEW_QUEUE);
    open();
    await screen.findByRole('button', { name: 'line-0001' });

    expect(screen.getByRole('button', { name: /next page/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /previous page/i })).toBeDisabled();
  });
});
