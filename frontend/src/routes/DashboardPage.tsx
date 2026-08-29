/** The starting point for a reviewer seeing Settlement Witness for the first time. */

import { useState } from 'react';
import { Link } from 'react-router-dom';

import { bootstrapDemo, getReviewQueue, listImports, listRuns } from '../api/client';
import { describeError } from '../api/errors';
import type { DemoBootstrapResult, RunSummary } from '../api/types';
import { formatMinorUnits, formatTimestamp } from '../format';
import { useLoad } from '../hooks';
import { ErrorNotice, Loading, OutcomeBadge, Panel, Stat, Stats } from '../components/ui';

const STATES = [
  {
    tone: 'resolved',
    glyph: '✓',
    name: 'Resolved',
    what: 'Every citation resolved to a stored fact and every required invariant held. This is the only answer that says a line is supported.',
  },
  {
    tone: 'exception',
    glyph: '!',
    name: 'Exception',
    what: 'The baseline reports a finding and does not resolve this line. Open its certificate to see the citations and the checks recorded for that finding.',
  },
  {
    tone: 'unknown',
    glyph: '?',
    name: 'Insufficient evidence',
    what: 'The backing does not support a determinate judgement, so none was made. Not a failure, and not a pass either.',
  },
] as const;

function DemoHero({
  latest,
  loading,
  result,
  error,
  onLoad,
}: {
  latest: RunSummary | null;
  loading: boolean;
  result: DemoBootstrapResult | null;
  error: unknown;
  onLoad: () => void;
}) {
  const destination = result?.run ?? latest;

  return (
    <section className="demo-hero" aria-labelledby="workspace-title">
      <div className="demo-hero__copy">
        <p className="eyebrow">Payment operations workspace</p>
        <h1 id="workspace-title">Know which settlements you can stand behind.</h1>
        <p>
          Settlement Witness follows a payout from payment events to settlement records and bank
          evidence. It surfaces uncertainty instead of hiding it inside a score.
        </p>
        <div className="demo-hero__actions">
          {destination ? (
            <Link className="button button--light" to={`/runs/${destination.run_id}`}>
              Open decision audit
            </Link>
          ) : (
            <button
              className="button button--light"
              type="button"
              disabled={loading}
              onClick={onLoad}
            >
              {loading ? 'Preparing demo…' : 'Load the interactive demo'}
            </button>
          )}
          <Link className="button button--ghost" to="/imports">
            Bring your own CSV
          </Link>
        </div>
        <p className="demo-hero__assurance">
          The walkthrough uses four bundled synthetic files. It does not upload anything from your
          device.
        </p>
        {error ? (
          <p className="demo-hero__error" role="alert">
            Could not prepare the walkthrough: {describeError(error)}
          </p>
        ) : null}
      </div>

      <ol className="demo-journey" aria-label="How Settlement Witness works">
        <li>
          <span className="demo-journey__step">01</span>
          <div>
            <strong>Bring in evidence</strong>
            <span>Payment, settlement, payout and bank records stay separate.</span>
          </div>
        </li>
        <li>
          <span className="demo-journey__step">02</span>
          <div>
            <strong>Run the checks</strong>
            <span>Every outcome carries its evidence and invariant certificate.</span>
          </div>
        </li>
        <li>
          <span className="demo-journey__step">03</span>
          <div>
            <strong>Act without rewriting history</strong>
            <span>Human review is a workflow trail, never an override button.</span>
          </div>
        </li>
      </ol>
    </section>
  );
}

export function DashboardPage() {
  const runs = useLoad(() => listRuns({ limit: 1 }), 'latest-run');
  const imports = useLoad(() => listImports({ limit: 5 }), 'recent-imports');
  const latest = runs.data?.runs[0] ?? null;
  const [demo, setDemo] = useState<DemoBootstrapResult | null>(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoError, setDemoError] = useState<unknown>(null);

  const review = useLoad(
    () => (latest === null ? Promise.resolve(null) : getReviewQueue(latest.run_id, { limit: 1 })),
    `review-queue|${latest?.run_id ?? 'none'}`,
  );
  const resolved = latest?.status_counts.RESOLVED ?? 0;
  const exceptions = latest?.status_counts.EXCEPTION ?? 0;
  const insufficient = latest?.status_counts.INSUFFICIENT_EVIDENCE ?? 0;

  const loadDemo = async () => {
    if (demoLoading) {
      return;
    }
    setDemoLoading(true);
    setDemoError(null);
    try {
      const prepared = await bootstrapDemo();
      setDemo(prepared);
      runs.reload();
      imports.reload();
    } catch (cause) {
      setDemoError(cause);
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <>
      <DemoHero
        latest={latest}
        loading={demoLoading}
        result={demo}
        error={demoError}
        onLoad={() => {
          void loadDemo();
        }}
      />

      {demo ? (
        <section className="demo-ready" aria-label="Walkthrough ready">
          <div>
            <p className="eyebrow">Walkthrough ready</p>
            <h2>
              {demo.created
                ? 'A real audit is ready to inspect.'
                : 'The walkthrough is already ready.'}
            </h2>
            <p>
              {demo.fixture_results.filter((item) => item.loaded_now).length} bundled files loaded ·{' '}
              {formatMinorUnits(demo.run.fact_count)} source records ·{' '}
              {formatMinorUnits(demo.run.decision_count)} settlement decisions
            </p>
          </div>
          <Link className="button" to={`/runs/${demo.run.run_id}`}>
            Inspect the evidence trail
          </Link>
        </section>
      ) : null}

      <section className="workspace-overview" aria-label="Current workspace">
        <div>
          <p className="eyebrow">Current workspace</p>
          <h2>{latest ? 'Decision snapshot' : 'Start with a guided example'}</h2>
          <p>
            {latest
              ? 'Open the audit to trace each decision back to its evidence, checks and review trail.'
              : 'Load the walkthrough once, then inspect the decisions and the two payout outcomes end to end.'}
          </p>
        </div>
        {latest ? (
          <Link className="text-link" to={`/runs/${latest.run_id}`}>
            View latest run <span aria-hidden="true">→</span>
          </Link>
        ) : null}
      </section>

      {runs.loading ? <Loading what="the latest workspace" /> : null}
      {runs.error ? (
        <ErrorNotice error={runs.error} onRetry={runs.reload} what="the latest workspace" />
      ) : null}

      {latest ? (
        <div className="grid grid--halves">
          <Panel
            title="Settlement decisions"
            note={`Recorded ${formatTimestamp(latest.created_at)}`}
            actions={
              <Link className="button button--quiet button--small" to={`/runs/${latest.run_id}`}>
                Open audit
              </Link>
            }
          >
            <Stats label="Run summary">
              <Stat label="Source records" value={formatMinorUnits(latest.fact_count)} />
              <Stat
                label="Settlement lines"
                value={formatMinorUnits(latest.settlement_line_count)}
              />
              <Stat label="Supported" value={formatMinorUnits(resolved)} tone="resolved" />
              <Stat label="Needs attention" value={formatMinorUnits(exceptions)} tone="exception" />
              <Stat
                label="Cannot decide yet"
                value={formatMinorUnits(insufficient)}
                tone="unknown"
              />
            </Stats>
            <p className="panel__note panel__note--spaced">
              Baseline {latest.baseline_version} · contract {latest.domain_schema_version} · parser{' '}
              {latest.parser_version}
            </p>
          </Panel>

          <Panel
            title="Review work"
            note="The lines the baseline did not resolve, with a human workflow beside—not inside—the decision."
            actions={
              <Link
                className="button button--quiet button--small"
                to={`/runs/${latest.run_id}/review`}
              >
                Open queue
              </Link>
            }
          >
            {review.loading ? <Loading what="the review queue" /> : null}
            {review.error ? (
              <ErrorNotice error={review.error} onRetry={review.reload} what="the review queue" />
            ) : null}
            {review.data ? (
              <>
                <Stats label="Review queue">
                  <Stat
                    label="Needs review"
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
                  <strong>Review never changes a decision.</strong>{' '}
                  {review.data.baseline_unchanged_note}
                </p>
              </>
            ) : null}
          </Panel>
        </div>
      ) : null}

      <Panel title="What each answer means" note="Read the certificate before acting on a line.">
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
        title="Recent evidence activity"
        note="Every import attempt has a receipt, including a refused one."
        actions={
          <Link className="button button--quiet button--small" to="/imports">
            Manage evidence
          </Link>
        }
      >
        {imports.loading ? <Loading what="import history" /> : null}
        {imports.error ? (
          <ErrorNotice error={imports.error} onRetry={imports.reload} what="import history" />
        ) : null}
        {!imports.loading && !imports.error && imports.data?.receipts.length === 0 ? (
          <p className="empty-copy">
            No evidence has been added yet. Load the walkthrough above or import your own CSV files.
          </p>
        ) : null}
        {imports.data && imports.data.receipts.length > 0 ? (
          <div className="table-scroll">
            <table>
              <caption>
                The {formatMinorUnits(Math.min(5, imports.data.total))} most recent attempts of{' '}
                {formatMinorUnits(imports.data.total)}.
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
