/**
 * One queue item: what the baseline concluded, and what people did about it.
 *
 * The layout carries the argument. The baseline status sits at the top, in the
 * same badge every other screen uses, and the workflow state sits below it in a
 * visibly different one. Between them is a sentence saying the second does not
 * change the first, and it is not dismissible.
 *
 * That separation is the whole design. A screen where a reviewer clicks
 * something and the status badge changes would be a screen that lies about what
 * this system can do, and the lie would be discovered by an auditor rather than
 * by a developer.
 *
 * Everything the server sends is rendered as text. React escapes by default,
 * and nothing here reaches for `dangerouslySetInnerHTML` or renders a note
 * through a Markdown component, so a note containing markup is shown as the
 * characters somebody typed.
 */

import { formatTimestamp, humanise } from '../format';
import { ACTION_LABELS, WORKFLOW_LABELS } from '../review';
import type { ReviewEventView, ReviewQueueItem } from '../api/types';
import { StatusBadge } from './ui';

/**
 * The badge for a workflow state.
 *
 * Deliberately not `StatusBadge`. They must not look alike, because they are
 * not alike: one says what the evidence supports and the other says who is
 * dealing with it.
 */
export function WorkflowBadge({ state }: { state: string }) {
  return (
    <span className="workflow-badge" data-state={state}>
      <span className="workflow-badge__dot" aria-hidden="true" />
      {WORKFLOW_LABELS[state] ?? humanise(state)}
    </span>
  );
}

/** The sentence that must appear wherever a workflow state appears. */
export function BaselineUnchangedNotice({ note }: { note: string }) {
  return (
    <p className="baseline-rule" role="note">
      <span className="baseline-rule__mark" aria-hidden="true">
        i
      </span>
      <span>
        <strong>Workflow is separate from the decision.</strong> Add evidence and run a new batch to
        change an outcome.
      </span>
      <span className="visually-hidden">
        Human workflow state does not change the baseline decision. {note}
      </span>
    </p>
  );
}

function TimelineEntry({ event }: { event: ReviewEventView }) {
  return (
    <li className="timeline__entry">
      <span className="timeline__seq" aria-hidden="true">
        {event.sequence}
      </span>
      <div>
        <p className="timeline__action">{ACTION_LABELS[event.action]?.label ?? event.action}</p>
        {event.note === null ? null : <p className="timeline__note">{event.note}</p>}
        <p className="timeline__when">
          Recorded {formatTimestamp(event.recorded_at)} · no reviewer recorded, this system has no
          sign-in
        </p>
      </div>
    </li>
  );
}

/** The ordered history of what people did about one decision. */
export function ReviewTimeline({ events }: { events: readonly ReviewEventView[] }) {
  if (events.length === 0) {
    return (
      <p className="timeline__empty">
        Nothing has been recorded against this line yet. It is in the queue because the baseline did
        not resolve it, not because anybody has looked at it.
      </p>
    );
  }
  return (
    <ol className="timeline">
      {[...events]
        .sort((left, right) => left.sequence - right.sequence)
        .map((event) => (
          <TimelineEntry key={event.event_id} event={event} />
        ))}
    </ol>
  );
}

/**
 * The header of the workspace: the conclusion, then the workflow, then the rule.
 *
 * In that order on purpose. A reader arriving at a closed item sees the
 * `EXCEPTION` first.
 */
export function ReviewHeader({ item }: { item: ReviewQueueItem }) {
  return (
    <div className="review-header" role="group" aria-label="Decision and workflow">
      <div className="review-header__row">
        <span className="review-header__label">Baseline decision</span>
        <StatusBadge status={item.baseline_status} />
        <span className="mono review-header__line">{item.decision.subject_settlement_line_id}</span>
      </div>
      <div className="review-header__row">
        <span className="review-header__label">Human workflow</span>
        <WorkflowBadge state={item.workflow_state} />
      </div>
      <BaselineUnchangedNotice note={item.baseline_unchanged_note} />
    </div>
  );
}
