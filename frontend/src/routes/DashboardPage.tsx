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

import { listImports, listRuns } from '../api/client';
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
    what: 'The evidence is there and a rule about it does not hold. A real finding, reported rather than smoothed away.',
  },
  {
    tone: 'unknown',
    glyph: '?',
    name: 'Insufficient evidence',
    what: 'The line cites something that is not in the store, so no judgement is possible. Not a failure, and not a pass either.',
  },
] as const;

export function DashboardPage() {
  const runs = useLoad(() => listRuns({ limit: 1 }), 'latest-run');
  const imports = useLoad(() => listImports({ limit: 5 }), 'recent-imports');

  const latest = runs.data?.runs[0] ?? null;
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
        <div className="states">
          {STATES.map((state) => (
            <div key={state.name} className="state-card">
              <span className={`badge badge--${state.tone}`}>
                <span className="badge__glyph" aria-hidden="true">
                  {state.glyph}
                </span>
                {state.name}
              </span>
              <p className="state-card__what">{state.what}</p>
            </div>
          ))}
        </div>
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
