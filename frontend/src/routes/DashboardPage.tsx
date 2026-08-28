/**
 * What the system is, and where the data currently stands.
 *
 * The empty state matters more than the populated one. With no facts loaded
 * there is nothing to report, and reporting zeroes in the same layout as real
 * counts would make an empty store look like a clean bill of health. So an
 * empty store gets an explanation and a next step instead of a dashboard of
 * noughts.
 */

import { Link } from 'react-router-dom';

import { getReviewQueue, listImports, listRuns } from '../api/client';
import { formatMinorUnits, formatTimestamp } from '../format';
import { useLoad } from '../hooks';
import {
  EmptyState,
  ErrorNotice,
  Loading,
  OutcomeBadge,
  Panel,
  Stat,
  Stats,
} from '../components/ui';

/**
 * The three answers, described as the contract actually defines them.
 *
 * The exception wording has been wrong twice, in two different directions, so
 * it is worth writing down what it may not say.
 *
 * It may not say a rule failed. A failed invariant means an exception, and an
 * exception does not mean a failed invariant: the baseline also raises one for
 * a lifecycle state it will not resolve on, such as a partial refund, with
 * every check passing. `line-0001` of the demo corpus is that case.
 *
 * It may not say the evidence was there either. `derive_status` reads the
 * exception codes before it looks at the citations, so a decision citing
 * nothing at all and carrying one ordinary code is an `EXCEPTION`. A domain
 * test pins that.
 *
 * What is true of every exception is that the backing carries a reported
 * finding or a failed invariant, and that the baseline will not resolve the
 * line. Everything else varies, which is what the certificate is for, so the
 * card points at it rather than guessing on its behalf.
 */
const STATES = [
  {
    tone: 'resolved',
    glyph: '✓',
    name: 'Resolved',
    what: 'Every citation resolved to a stored fact and every required invariant held. This is the only state that says a line is supported.',
  },
  {
    tone: 'exception',
    glyph: '!',
    name: 'Exception',
    what: 'The baseline reports a finding and does not resolve this line. Its certificate shows the citations and the checks recorded for that finding, including any that are missing.',
  },
  {
    tone: 'unknown',
    glyph: '?',
    name: 'Insufficient evidence',
    what: 'The backing does not support a determinate judgement, so none was made. Not a failure, and not a pass either.',
  },
] as const;

export function DashboardPage() {
  const runs = useLoad(() => listRuns({ limit: 1 }), 'latest-run');
  const imports = useLoad(() => listImports({ limit: 5 }), 'recent-imports');

  const latest = runs.data?.runs[0] ?? null;
  // Asked for only once there is a run to ask about, and with a page size of
  // one because the counts describe the whole queue rather than the page. The
  // key carries the run ID so the request is remade when the latest run
  // changes, rather than reporting the previous run's queue under the new one.
  const review = useLoad(
    () =>
      latest === null
        ? Promise.resolve(null)
        : getReviewQueue(latest.run_id, { limit: 1 }).then((page) => page),
    `review-queue|${latest?.run_id ?? 'none'}`,
  );
  const resolved = latest?.status_counts.RESOLVED ?? 0;
  const exceptions = latest?.status_counts.EXCEPTION ?? 0;
  const insufficient = latest?.status_counts.INSUFFICIENT_EVIDENCE ?? 0;

  return (
    <>
      <div className="page__head">
        <div>
          <h1>Evidence-first settlement reconciliation</h1>
          <p className="page__lede">
            A settlement line is resolved only when the source records it cites are in the store and
            every required invariant about them holds. Anything else is reported as what it is,
            rather than folded into a success rate.
          </p>
        </div>
      </div>

      <Panel title="The three answers a line can get">
        <ul className="states">
          {STATES.map((state) => (
            <li key={state.name} className="state-card">
              <span className={`badge badge--${state.tone}`}>
                <span className="badge__glyph" aria-hidden="true">
                  {state.glyph}
                </span>
                {state.name}
              </span>
              <p className="state-card__what">{state.what}</p>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Latest reconciliation run"
        actions={
          latest ? (
            <Link className="button button--quiet button--small" to={`/runs/${latest.run_id}`}>
              Audit this run
            </Link>
          ) : null
        }
      >
        {runs.loading ? <Loading what="the latest run" /> : null}
        {runs.error ? (
          <ErrorNotice error={runs.error} onRetry={runs.reload} what="the latest run" />
        ) : null}
        {!runs.loading && !runs.error && latest === null ? (
          <EmptyState
            title="No run has been recorded yet"
            actions={
              <>
                <Link className="button" to="/imports">
                  Import evidence
                </Link>
                <Link className="button button--quiet" to="/runs">
                  Go to runs
                </Link>
              </>
            }
          >
            Nothing has been reconciled, so there is nothing to report. Import the three example
            documents first, then create a run.
          </EmptyState>
        ) : null}
        {latest ? (
          <>
            <Stats label="Run summary">
              <Stat label="Source facts" value={formatMinorUnits(latest.fact_count)} />
              <Stat
                label="Settlement lines"
                value={formatMinorUnits(latest.settlement_line_count)}
              />
              <Stat label="Resolved" value={formatMinorUnits(resolved)} tone="resolved" />
              <Stat label="Exceptions" value={formatMinorUnits(exceptions)} tone="exception" />
              <Stat
                label="Insufficient evidence"
                value={formatMinorUnits(insufficient)}
                tone="unknown"
              />
            </Stats>
            <p className="panel__note" style={{ marginTop: 12 }}>
              Recorded {formatTimestamp(latest.created_at)} · baseline {latest.baseline_version} ·
              contract {latest.domain_schema_version} · parser {latest.parser_version}
            </p>
          </>
        ) : null}
      </Panel>

      <Panel
        title="Human review queue"
        note="The lines the latest run did not resolve, and what people are doing about them."
        actions={
          latest ? (
            <Link
              className="button button--quiet button--small"
              to={`/runs/${latest.run_id}/review`}
            >
              Open the review queue
            </Link>
          ) : null
        }
      >
        {latest === null ? (
          <p className="panel__note">
            There is no run yet, so there is nothing to review. A review queue is built from a
            recorded run, never from the fact store directly.
          </p>
        ) : null}
        {latest && review.loading ? <Loading what="the review queue" /> : null}
        {latest && review.error ? (
          <ErrorNotice error={review.error} onRetry={review.reload} what="the review queue" />
        ) : null}
        {review.data ? (
          <>
            <Stats label="Review queue">
              <Stat
                label="Needing review"
                value={formatMinorUnits(review.data.total)}
                tone="exception"
              />
              <Stat label="Still open" value={formatMinorUnits(review.data.open_total)} />
              <Stat
                label="Closed without override"
                value={formatMinorUnits(review.data.total - review.data.open_total)}
              />
            </Stats>
            <p className="notice notice--warn baseline-note" role="note">
              <strong>Human workflow state does not change the baseline decision.</strong>{' '}
              {review.data.baseline_unchanged_note}
            </p>
          </>
        ) : null}
      </Panel>

      <Panel
        title="Recent import attempts"
        actions={
          <Link className="button button--quiet button--small" to="/imports">
            Import evidence
          </Link>
        }
      >
        {imports.loading ? <Loading what="import history" /> : null}
        {imports.error ? (
          <ErrorNotice error={imports.error} onRetry={imports.reload} what="import history" />
        ) : null}
        {!imports.loading && !imports.error && imports.data?.receipts.length === 0 ? (
          <EmptyState
            title="Nothing has been imported"
            actions={
              <Link className="button" to="/imports">
                Import the example documents
              </Link>
            }
          >
            Load payment events, settlement lines and payouts to give the reconciler something to
            work from.
          </EmptyState>
        ) : null}
        {imports.data && imports.data.receipts.length > 0 ? (
          <div className="table-scroll">
            <table>
              <caption>
                The {formatMinorUnits(Math.min(5, imports.data.total))} most recent attempts of{' '}
                {formatMinorUnits(imports.data.total)}. Every attempt leaves a receipt, including a
                refused one.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Read as</th>
                  <th scope="col">Outcome</th>
                  <th scope="col" className="num">
                    Stored
                  </th>
                  <th scope="col">Received</th>
                </tr>
              </thead>
              <tbody>
                {imports.data.receipts.map((receipt) => (
                  <tr key={receipt.receipt_id}>
                    <td className="mono">{receipt.document_name}</td>
                    <td>{receipt.source_record_type}</td>
                    <td>
                      <OutcomeBadge outcome={receipt.outcome} />
                    </td>
                    <td className="num">{formatMinorUnits(receipt.accepted_count)}</td>
                    <td>{formatTimestamp(receipt.received_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>
    </>
  );
}
