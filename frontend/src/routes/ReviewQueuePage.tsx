/**
 * The review queue for one run: the lines the baseline would not resolve.
 *
 * Two panes, like the audit screen, because the question here is also "why that
 * one". The list on the left shows the baseline status and the workflow state
 * in two visibly different badges; the workspace on the right shows the
 * certificate, the timeline, and the four things a person may record.
 *
 * There is no action here that resolves anything, and there is no arrangement
 * of clicks that changes a status. That is stated on screen rather than left to
 * be inferred, because a reviewer who believes closing an item settles a line
 * will close items believing they are settled.
 *
 * Every string from the server is rendered as text. Nothing here reaches for
 * `dangerouslySetInnerHTML`, and there is no Markdown renderer in this
 * application, so a note is shown as the characters somebody typed.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { appendReviewEvent, getReviewQueue } from '../api/client';
import { REVIEW_ACTIONS } from '../api/types';
import type { ReviewAction, ReviewQueueItem, ReviewQueuePage } from '../api/types';
import { describeError } from '../api/errors';
import { formatMinorUnits } from '../format';
import { useLoad } from '../hooks';
import { DecisionCertificate } from '../components/DecisionCertificate';
import {
  BaselineUnchangedNotice,
  ReviewHeader,
  ReviewTimeline,
  WorkflowBadge,
} from '../components/ReviewWorkspace';
import { ACTION_LABELS } from '../review';
import {
  EmptyState,
  ErrorNotice,
  Loading,
  Panel,
  Stat,
  Stats,
  StatusBadge,
} from '../components/ui';

const PAGE_SIZE = 20;

/**
 * A key for one command attempt.
 *
 * Generated per submission rather than per component, so a reviewer who presses
 * the button twice sends two commands and the second is a new event, while a
 * retry of a request that timed out reuses nothing and cannot be mistaken for
 * the first. `crypto.randomUUID` is available in every browser this targets and
 * in the test environment.
 */
function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

function ReviewForm({
  item,
  runId,
  onRecorded,
}: {
  item: ReviewQueueItem;
  runId: string;
  onRecorded: () => void;
}) {
  const [action, setAction] = useState<ReviewAction>('ACKNOWLEDGED');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [recorded, setRecorded] = useState<string | null>(null);

  async function submit(event: React.SyntheticEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    setRecorded(null);
    try {
      const receipt = await appendReviewEvent(runId, item.decision.decision_id, {
        action,
        decisionFingerprint: item.decision_fingerprint,
        idempotencyKey: newIdempotencyKey(),
        note,
      });
      setNote('');
      setRecorded(receipt.baseline_status);
      onRecorded();
    } catch (cause) {
      setFailure(cause);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="review-form" onSubmit={(event) => void submit(event)}>
      <fieldset className="field" disabled={busy}>
        <legend className="field__label">Record a workflow action</legend>
        <p className="field__hint">
          None of these changes the decision above. There is no approve, resolve or override action,
          because this system cannot settle a line from a button. A line is settled by a source
          record that supports it, imported and reconciled into a new run.
        </p>
        {REVIEW_ACTIONS.map((option) => (
          <label key={option} className="radio">
            <input
              type="radio"
              name="review-action"
              value={option}
              checked={action === option}
              onChange={() => {
                setAction(option);
              }}
            />
            <span>
              <span className="radio__label">{ACTION_LABELS[option]?.label ?? option}</span>
              <span className="radio__what">{ACTION_LABELS[option]?.what ?? ''}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <label className="field" htmlFor="review-note">
        <span className="field__label">Note (optional)</span>
        <span className="field__hint">
          A sentence for whoever reads this next. Stored and shown as plain text.
        </span>
        <textarea
          id="review-note"
          name="review-note"
          rows={2}
          maxLength={500}
          value={note}
          disabled={busy}
          onChange={(event) => {
            setNote(event.target.value);
          }}
        />
      </label>

      <div className="form-row">
        <button type="submit" className="button" disabled={busy}>
          {busy ? 'Recording…' : 'Record this action'}
        </button>
      </div>

      {recorded === null ? null : (
        <p className="notice notice--info" role="status">
          Recorded. The baseline decision is still <strong>{recorded}</strong>, and nothing about it
          has changed.
        </p>
      )}
      {failure === null ? null : (
        <p className="notice notice--error" role="alert">
          {describeError(failure)}
        </p>
      )}
    </form>
  );
}

export function ReviewQueuePage() {
  const { runId = '' } = useParams<{ runId: string }>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);

  const queue = useLoad(
    () => getReviewQueue(runId, { limit: PAGE_SIZE }),
    `${runId}|review|${String(reloads)}`,
  );

  // The last page that arrived, kept across a reload.
  //
  // Recording an action reloads the queue, and `useLoad` reports no data while
  // a request is in flight. Rendering the loading state then would blank the
  // workspace at the exact moment somebody pressed a button, taking the
  // confirmation with it. React documents adjusting state during a render for
  // this: the component re-runs immediately and nothing is committed twice.
  const [shown, setShown] = useState<ReviewQueuePage | null>(null);
  if (queue.data !== null && queue.data !== shown) {
    setShown(queue.data);
  }

  const items = shown?.items ?? [];
  const selected = items.find((one) => one.decision.decision_id === selectedId) ?? items[0] ?? null;

  if (queue.loading && shown === null) {
    return <Loading what="the review queue" />;
  }

  if (queue.error) {
    return (
      <>
        <div className="page__head">
          <h1>Review queue</h1>
        </div>
        <ErrorNotice error={queue.error} onRetry={queue.reload} what="the review queue" />
        <p>
          <Link to="/runs">Back to runs</Link>
        </p>
      </>
    );
  }

  if (shown === null) {
    return null;
  }

  return (
    <>
      <div className="page__head">
        <div>
          <h1>Review queue</h1>
          <p className="page__lede">
            The settlement lines this run did not resolve. A reviewer records what is being done
            about each one. Nothing recorded here changes what the baseline concluded.
          </p>
        </div>
        <Link className="button button--quiet button--small" to={`/runs/${runId}`}>
          Audit this run
        </Link>
      </div>

      <BaselineUnchangedNotice note={shown.baseline_unchanged_note} />

      <Stats label="Queue summary">
        <Stat label="Needing review" value={formatMinorUnits(shown.total)} tone="exception" />
        <Stat label="Still open" value={formatMinorUnits(shown.open_total)} />
        <Stat
          label="Closed without override"
          value={formatMinorUnits(shown.total - shown.open_total)}
        />
      </Stats>

      {items.length === 0 ? (
        <EmptyState
          title="Nothing in this run needs a person"
          actions={
            <Link className="button button--quiet" to={`/runs/${runId}`}>
              Audit this run
            </Link>
          }
        >
          Every line this run judged was resolved, so there is no exception and no unknown to work
          through. An empty queue is a result, not a missing screen.
        </EmptyState>
      ) : (
        <div className="grid grid--audit">
          <Panel
            title="Lines needing a person"
            note="Ordered by settlement line ID, so the order does not move when somebody acts on an item."
          >
            <div className="table-scroll">
              <table>
                <caption>
                  {formatMinorUnits(items.length)} of {formatMinorUnits(shown.total)} shown. The
                  baseline status and the workflow state are two different facts.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Settlement line</th>
                    <th scope="col">Baseline decision</th>
                    <th scope="col">Human workflow</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const isSelected = selected?.decision.decision_id === item.decision.decision_id;
                    return (
                      <tr
                        key={item.decision.decision_id}
                        className={`is-selectable${isSelected ? ' is-selected' : ''}`}
                      >
                        <td>
                          <button
                            type="button"
                            className="row-button mono"
                            aria-pressed={isSelected}
                            onClick={() => {
                              setSelectedId(item.decision.decision_id);
                            }}
                          >
                            {item.decision.subject_settlement_line_id}
                          </button>
                        </td>
                        <td>
                          <StatusBadge status={item.baseline_status} />
                        </td>
                        <td>
                          <WorkflowBadge state={item.workflow_state} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>

          {selected === null ? null : (
            <div className="grid">
              <Panel title="Review workspace">
                <ReviewHeader item={selected} />
                <h3>What people have recorded</h3>
                <ReviewTimeline events={selected.events} />
                <ReviewForm
                  key={selected.decision.decision_id}
                  item={selected}
                  runId={runId}
                  onRecorded={() => {
                    setReloads((previous) => previous + 1);
                  }}
                />
              </Panel>
              <DecisionCertificate decision={selected.decision} />
            </div>
          )}
        </div>
      )}
    </>
  );
}
