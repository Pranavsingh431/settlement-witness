/**
 * Creating a run, and the history of runs already recorded.
 *
 * Creating one is idempotent. The same facts under the same rule versions
 * produce the same run key, so asking again returns the run already recorded
 * rather than writing a second row describing the same conclusion. The API says
 * which happened with 201 or 200, and this screen repeats that distinction
 * rather than reporting both as "done", because "we recorded a new conclusion"
 * and "that conclusion already existed" are different facts.
 */

import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';

import { createRun, listRuns } from '../api/client';
import { ApiError, describeError } from '../api/errors';
import type { RunCreation } from '../api/types';
import { formatMinorUnits, formatTimestamp, shortHash } from '../format';
import { useLoad } from '../hooks';
import { EmptyState, ErrorNotice, Loading, Panel } from '../components/ui';

export function RunsPage() {
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState<RunCreation | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const runs = useLoad(() => listRuns({ limit: 20 }), `runs|${String(reloadKey)}`);

  const reconcile = useCallback(async () => {
    if (creating) {
      return;
    }
    setCreating(true);
    setFailure(null);
    setResult(null);
    try {
      const created = await createRun();
      setResult(created);
      setReloadKey((previous) => previous + 1);
    } catch (cause) {
      setFailure(cause);
    } finally {
      setCreating(false);
    }
  }, [creating]);

  const noFacts = failure instanceof ApiError && failure.code === 'no_facts';

  return (
    <>
      <div className="page__head">
        <div>
          <h1>Reconciliation runs</h1>
          <p className="page__lede">
            A run reconciles every stored source fact and records what it concluded. Runs are
            immutable: new evidence produces a new run beside the old one rather than changing it.
          </p>
        </div>
        <button
          type="button"
          className="button"
          disabled={creating}
          onClick={() => {
            // Async handler, nothing to return to the DOM.
            void reconcile();
          }}
        >
          {creating ? 'Reconciling…' : 'Reconcile stored facts'}
        </button>
      </div>

      <div aria-live="polite">
        {result ? (
          <div className={`notice ${result.created ? 'notice--good' : 'notice--info'}`}>
            <p className="notice__title">
              {result.created
                ? 'A new immutable run was recorded.'
                : 'This snapshot already had a run.'}
            </p>
            <p className="notice__body">
              {result.created
                ? 'The stored facts were reconciled and the conclusion was written.'
                : 'The same facts under the same rule versions were already reconciled, so the existing run was returned rather than a duplicate written.'}{' '}
              <Link to={`/runs/${result.run.run_id}`}>
                Audit run {shortHash(result.run.run_id)}
              </Link>
            </p>
          </div>
        ) : null}

        {noFacts ? (
          <div className="notice notice--warn" role="alert">
            <p className="notice__title">There is nothing to reconcile.</p>
            <p className="notice__body">
              The store holds no accepted source facts yet.{' '}
              <Link to="/imports">Import evidence first</Link>, then reconcile.
            </p>
          </div>
        ) : null}

        {failure && !noFacts ? (
          <div className="notice notice--error" role="alert">
            <p className="notice__title">The run was not created.</p>
            <p className="notice__body">{describeError(failure)}</p>
          </div>
        ) : null}
      </div>

      <Panel title="Recorded runs" note="Newest first.">
        {runs.loading ? <Loading what="runs" /> : null}
        {runs.error ? <ErrorNotice error={runs.error} onRetry={runs.reload} what="runs" /> : null}

        {runs.data?.runs.length === 0 ? (
          <EmptyState
            title="No runs recorded"
            actions={
              <Link className="button" to="/imports">
                Import evidence
              </Link>
            }
          >
            Import the example documents, then reconcile them. Nothing is inferred until there are
            facts to reconcile.
          </EmptyState>
        ) : null}

        {runs.data && runs.data.runs.length > 0 ? (
          <div className="table-scroll">
            <table>
              <caption>
                {formatMinorUnits(runs.data.total)} run(s) recorded. Counts describe the whole run.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Snapshot</th>
                  <th scope="col">Versions</th>
                  <th scope="col" className="num">
                    Facts
                  </th>
                  <th scope="col" className="num">
                    Resolved
                  </th>
                  <th scope="col" className="num">
                    Exceptions
                  </th>
                  <th scope="col" className="num">
                    Insufficient
                  </th>
                  <th scope="col">Recorded</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link className="mono" to={`/runs/${run.run_id}`}>
                        {shortHash(run.run_id, 10)}
                      </Link>
                    </td>
                    <td className="mono" title={run.snapshot_fingerprint}>
                      {shortHash(run.snapshot_fingerprint, 10)}
                    </td>
                    <td className="panel__note">
                      baseline {run.baseline_version} · contract {run.domain_schema_version} ·
                      parser {run.parser_version}
                    </td>
                    <td className="num">{formatMinorUnits(run.fact_count)}</td>
                    <td className="num">{formatMinorUnits(run.status_counts.RESOLVED ?? 0)}</td>
                    <td className="num">{formatMinorUnits(run.status_counts.EXCEPTION ?? 0)}</td>
                    <td className="num">
                      {formatMinorUnits(run.status_counts.INSUFFICIENT_EVIDENCE ?? 0)}
                    </td>
                    <td>{formatTimestamp(run.created_at)}</td>
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
