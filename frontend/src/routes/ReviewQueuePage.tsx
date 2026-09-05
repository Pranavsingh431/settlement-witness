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
import { Link, useParams, useSearchParams } from 'react-router-dom';

import {
  appendReviewEvent,
  evidenceRequestDownloadUrl,
  getReviewQueue,
  getReviewItem,
} from '../api/client';
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
 * One command, with the key it will be sent under.
 *
 * The key belongs to the command rather than to the click. That is the whole
 * point: a request that fails may still have been recorded, because the answer
 * can be lost after the server has written the row. Sending a new key on the
 * next click would then append a second event for one intended action, and the
 * reviewer would have no way to tell.
 *
 * So the command is kept while it is uncertain, and retrying the same input
 * sends the same key, which the server answers with the original event. A
 * different command gets a different key, because it is a different action.
 */
interface PendingCommand {
  readonly action: ReviewAction;
  /** Normalised, matching what the server stores and fingerprints. */
  readonly note: string;
  readonly decisionId: string;
  readonly fingerprint: string;
  readonly key: string;
}

/** What the form is currently asking for, without a key yet. */
type CommandInput = Omit<PendingCommand, 'key'>;

function sameCommand(pending: PendingCommand, wanted: CommandInput): boolean {
  return (
    pending.action === wanted.action &&
    pending.note === wanted.note &&
    pending.decisionId === wanted.decisionId &&
    pending.fingerprint === wanted.fingerprint
  );
}

/**
 * A key for one command.
 *
 * `crypto.randomUUID` is available in every browser this targets and in the
 * test environment. The value is not a credential and nothing authenticates
 * with it; it only has to be unique per intended action.
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
  const [pending, setPending] = useState<PendingCommand | null>(null);

  const wanted: CommandInput = {
    action,
    note: note.trim(),
    decisionId: item.decision.decision_id,
    fingerprint: item.decision_fingerprint,
  };
  // True when the next submission would repeat a command whose outcome is
  // unknown. Derived rather than stored, so editing the note or choosing
  // another action stops it being a retry the moment the input changes.
  const retrying = pending !== null && sameCommand(pending, wanted);

  async function submit(event: React.SyntheticEvent): Promise<void> {
    event.preventDefault();
    // The command is decided here, before the request, so the key is a property
    // of what is being asked for rather than of when the button was pressed.
    const command: PendingCommand =
      pending !== null && sameCommand(pending, wanted)
        ? pending
        : { ...wanted, key: newIdempotencyKey() };
    setPending(command);
    setBusy(true);
    setFailure(null);
    setRecorded(null);
    try {
      const receipt = await appendReviewEvent(runId, command.decisionId, {
        action: command.action,
        decisionFingerprint: command.fingerprint,
        idempotencyKey: command.key,
        note: command.note,
      });
      // Confirmed. The command is finished, so the next one is a new command
      // and gets a new key.
      setPending(null);
      setNote('');
      setRecorded(receipt.baseline_status);
      onRecorded();
    } catch (cause) {
      // Kept. Whether the server recorded it is exactly what is not known.
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
          Record progress on this case. These actions update the workflow only. To change the
          financial result, add supporting evidence and reconcile a new batch.
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
          {busy ? 'Recording…' : retrying ? 'Retry this same action' : 'Record this action'}
        </button>
      </div>

      {retrying && !busy ? (
        <p className="notice notice--info" role="note">
          <strong>This retries the same action, not a second one.</strong> The request above may
          have reached the server before the answer was lost, so it is sent again under the same
          key. If it was recorded, you get that event back rather than a duplicate. Change the
          action or the note and it becomes a different action instead.
        </p>
      ) : null}

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
  const [params, setParams] = useSearchParams();
  const focusedId = params.get('decision');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloads, setReloads] = useState(0);

  const queue = useLoad(
    () => getReviewQueue(runId, { limit: PAGE_SIZE, offset }),
    `${runId}|review|${String(offset)}|${String(reloads)}`,
  );
  // A desk link names one exact case. Fetch it directly, even when it lives
  // beyond page one. A failed lookup must never silently open a different case.
  const focused = useLoad(
    () => (focusedId ? getReviewItem(runId, focusedId) : Promise.resolve(null)),
    `${runId}|${focusedId ?? ''}|${String(reloads)}`,
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
  // A selection is kept only while the item it names is on the page in front of
  // the reviewer. Paging away and back would otherwise resurrect a selection
  // they had left behind, and the workspace would be showing a line the list is
  // not highlighting.
  if (
    selectedId !== null &&
    items.length > 0 &&
    !items.some((one) => one.decision.decision_id === selectedId)
  ) {
    setSelectedId(null);
  }
  const selected = focusedId
    ? focused.data
    : (items.find((one) => one.decision.decision_id === selectedId) ?? items[0] ?? null);

  const total = shown?.total ?? 0;
  const onFirstPage = offset === 0;
  const onLastPage = offset + items.length >= total;

  function goTo(next: number): void {
    // The selection is not cleared here. The check above owns that, and owning
    // it in one place means it also covers a page that comes back without the
    // selected item for some other reason, such as a reload after an action.
    setOffset(next);
    setParams({}, { replace: true });
  }

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
            Keep track of who needs evidence and what happens next. Follow-ups record progress; they
            do not change the settlement result.
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
            note="Ordered by settlement line. Recording an action keeps your place in the queue."
          >
            <div className="table-scroll">
              <table>
                <caption>
                  Showing {formatMinorUnits(offset + 1)} to{' '}
                  {formatMinorUnits(offset + items.length)} of {formatMinorUnits(shown.total)}. The
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
                              setParams({}, { replace: true });
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
            <nav className="pager" aria-label="Review queue pages">
              <button
                type="button"
                className="button button--quiet button--small"
                disabled={onFirstPage}
                onClick={() => {
                  goTo(Math.max(0, offset - PAGE_SIZE));
                }}
              >
                Previous page
              </button>
              {/*
                Plain text, not a live region. The table caption above carries
                the same range and changes with the page, and two live regions
                competing on one screen is noise rather than access.
              */}
              <p className="pager__where">
                Items {formatMinorUnits(offset + 1)} to {formatMinorUnits(offset + items.length)} of{' '}
                {formatMinorUnits(shown.total)}
              </p>
              <button
                type="button"
                className="button button--quiet button--small"
                disabled={onLastPage}
                onClick={() => {
                  goTo(offset + PAGE_SIZE);
                }}
              >
                Next page
              </button>
            </nav>
          </Panel>

          {focusedId && focused.loading ? <Loading what="the selected case" /> : null}
          {focusedId && focused.error ? (
            <ErrorNotice error={focused.error} onRetry={focused.reload} what="the selected case" />
          ) : null}
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
              <DecisionCertificate
                decision={selected.decision}
                evidenceRequestHref={evidenceRequestDownloadUrl(
                  runId,
                  selected.decision.decision_id,
                )}
              />
            </div>
          )}
        </div>
      )}
    </>
  );
}
