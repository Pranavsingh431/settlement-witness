/**
 * Auditing one run: every decision, and why each came out as it did.
 *
 * The list and the certificate sit side by side because the question a person
 * has here is "why that one", and making them navigate away and back to ask it
 * about the next line turns an audit into a chore.
 *
 * Filters narrow the decisions and never the run's own counts, which always
 * describe the whole run. The screen says which view is on show, so a narrowed
 * list cannot be mistaken for the complete one.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  createBankFinalityAudit,
  evidenceRequestDownloadUrl,
  getBankFinalityAudit,
  getRun,
  getWorkboard,
  listBankFinalityAudits,
} from '../api/client';
import { DECISION_STATUSES } from '../api/types';
import type { RunWorkboard } from '../api/types';
import { formatMinorUnits, formatTimestamp } from '../format';
import { useLoad } from '../hooks';
import type { Loadable } from '../hooks';
import { DecisionCertificate } from '../components/DecisionCertificate';
import { BankFinalityCertificateView, SeparateConclusionsNotice } from '../components/BankFinality';
import {
  EmptyState,
  ErrorNotice,
  Facts,
  Loading,
  Panel,
  Stat,
  Stats,
  StatusBadge,
} from '../components/ui';
import { describeError } from '../api/errors';

export function RunAuditPage() {
  const { runId = '' } = useParams<{ runId: string }>();
  const [status, setStatus] = useState('');
  const [exceptionCode, setExceptionCode] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const detail = useLoad(
    () =>
      getRun(runId, {
        status: status || undefined,
        exception_code: exceptionCode || undefined,
      }),
    `${runId}|${status}|${exceptionCode}`,
  );
  const workboard = useLoad(() => getWorkboard(runId), `${runId}|workboard`);

  const run = detail.data?.run ?? null;
  const decisions = detail.data?.decisions ?? [];
  const selected = decisions.find((one) => one.decision_id === selectedId) ?? decisions[0] ?? null;
  const exceptionCodes = Object.keys(run?.exception_counts ?? {}).sort();

  if (detail.loading && run === null) {
    return <Loading what="this run" />;
  }

  if (detail.error) {
    return (
      <>
        <div className="page__head">
          <h1>Run audit</h1>
        </div>
        <ErrorNotice error={detail.error} onRetry={detail.reload} what="this run" />
        <p>
          <Link to="/runs">Back to runs</Link>
        </p>
      </>
    );
  }

  if (run === null) {
    return null;
  }

  return (
    <>
      <section className="audit-hero" aria-labelledby="audit-title">
        <div>
          <p className="eyebrow">Recorded reconciliation snapshot</p>
          <h1 id="audit-title">Run audit</h1>
          <p>
            Every settlement line this run judged, with the evidence it cited and the invariants it
            checked.
          </p>
        </div>
        <div className="audit-hero__actions">
          <Link className="button button--quiet button--small" to={`/runs/${runId}/review`}>
            Review queue
          </Link>
          <Link className="button button--quiet button--small" to="/runs">
            All runs
          </Link>
        </div>
      </section>

      <section className="audit-overview" aria-labelledby="audit-overview-title">
        <header className="section-heading section-heading--compact">
          <div>
            <p className="eyebrow">Whole-run summary</p>
            <h2 id="audit-overview-title">What this snapshot concluded</h2>
            <p>These counts describe the whole run, never a filtered view.</p>
          </div>
          <span className="section-heading__meta">Immutable record</span>
        </header>
        <Stats label="Run summary">
          <Stat label="Source facts" value={formatMinorUnits(run.fact_count)} />
          <Stat label="Settlement lines" value={formatMinorUnits(run.settlement_line_count)} />
          <Stat label="Decisions" value={formatMinorUnits(run.decision_count)} />
          <Stat
            label="Resolved"
            value={formatMinorUnits(run.status_counts.RESOLVED ?? 0)}
            tone="resolved"
          />
          <Stat
            label="Exceptions"
            value={formatMinorUnits(run.status_counts.EXCEPTION ?? 0)}
            tone="exception"
          />
          <Stat
            label="Insufficient evidence"
            value={formatMinorUnits(run.status_counts.INSUFFICIENT_EVIDENCE ?? 0)}
            tone="unknown"
          />
        </Stats>
        <Facts
          items={[
            ['Run ID', <span className="mono">{run.run_id}</span>],
            ['Snapshot fingerprint', <span className="hash">{run.snapshot_fingerprint}</span>],
            ['Baseline version', run.baseline_version],
            ['Domain contract version', run.domain_schema_version],
            ['Parser version', run.parser_version],
            ['As of', formatTimestamp(run.as_of)],
            ['Recorded at', formatTimestamp(run.created_at)],
          ]}
        />
      </section>

      <WorkboardPanel
        state={workboard}
        onSelect={(decisionId) => {
          setStatus('');
          setExceptionCode('');
          setSelectedId(decisionId);
        }}
      />

      <div className="audit-workspace">
        <section className="decision-navigator" aria-labelledby="decision-ledger-title">
          <header className="section-heading section-heading--compact">
            <div>
              <p className="eyebrow">Decision ledger</p>
              <h2 id="decision-ledger-title">Choose a settlement line</h2>
              <p>
                {detail.data?.filtered
                  ? 'Filtered. The counts above still describe the whole run.'
                  : 'Every decision in this run.'}
              </p>
            </div>
            {selected ? (
              <span className="section-heading__meta">
                Reading <span className="mono">{selected.subject_settlement_line_id}</span>
              </span>
            ) : null}
          </header>
          <div className="ledger-filters ledger-filters--audit" aria-label="Decision filters">
            <div className="field">
              <label className="field__label" htmlFor="decision-status">
                Status
              </label>
              <select
                id="decision-status"
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setSelectedId(null);
                }}
              >
                <option value="">Any status</option>
                {DECISION_STATUSES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field__label" htmlFor="decision-exception">
                Exception code
              </label>
              <select
                id="decision-exception"
                value={exceptionCode}
                onChange={(event) => {
                  setExceptionCode(event.target.value);
                  setSelectedId(null);
                }}
              >
                <option value="">Any exception</option>
                {exceptionCodes.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="button button--quiet button--small"
              disabled={status === '' && exceptionCode === ''}
              onClick={() => {
                setStatus('');
                setExceptionCode('');
                setSelectedId(null);
              }}
            >
              Clear filters
            </button>
          </div>

          {detail.loading ? <Loading what="decisions" /> : null}

          {!detail.loading && decisions.length === 0 ? (
            <EmptyState title="No decision matches these filters">
              Widen the filters to see the rest of this run.
            </EmptyState>
          ) : null}

          {!detail.loading && decisions.length > 0 ? (
            <div className="table-scroll">
              <table>
                <caption>
                  {formatMinorUnits(decisions.length)} decision(s) shown. Select one to read its
                  certificate.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Settlement line</th>
                    <th scope="col">Status</th>
                    <th scope="col">Exceptions</th>
                    <th scope="col" className="num">
                      Evidence
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {decisions.map((decision) => {
                    const isSelected = selected?.decision_id === decision.decision_id;
                    return (
                      <tr
                        key={decision.decision_id}
                        className={`is-selectable${isSelected ? ' is-selected' : ''}`}
                      >
                        <td>
                          <button
                            type="button"
                            className="row-button mono"
                            aria-pressed={isSelected}
                            onClick={() => {
                              setSelectedId(decision.decision_id);
                            }}
                          >
                            {decision.subject_settlement_line_id}
                            <span className="visually-hidden">
                              {' '}
                              — show the certificate for this line
                            </span>
                          </button>
                        </td>
                        <td>
                          <StatusBadge status={decision.status} />
                        </td>
                        <td>
                          {decision.exception_codes.length === 0 ? (
                            <span className="panel__note">None</span>
                          ) : (
                            <div className="chips">
                              {decision.exception_codes.map((code) => (
                                <span key={code} className="chip chip--exception">
                                  {code}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className="num">
                          {decision.verified_evidence_count}/{decision.evidence.length}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>

        <Panel title="Certificate" note="Why this line was decided the way it was.">
          <div id="decision-certificate">
            {selected ? (
              <DecisionCertificate
                decision={selected}
                evidenceRequestHref={evidenceRequestDownloadUrl(runId, selected.decision_id)}
              />
            ) : (
              <EmptyState title="No decision selected">
                Choose a settlement line to read the evidence and invariants behind its decision.
              </EmptyState>
            )}
          </div>
        </Panel>
      </div>

      <BankFinalityPanel snapshotFingerprint={run.snapshot_fingerprint} />
    </>
  );
}

/**
 * Turn unresolved decisions into a human-sized queue without inventing an FX
 * conversion or a cross-currency cash total. The value is deliberately called
 * a declared settlement net: it tells an operator where to start, while bank
 * finality remains the separate proof that a payout reached the merchant.
 */
function WorkboardPanel({
  state,
  onSelect,
}: {
  readonly state: Loadable<RunWorkboard>;
  readonly onSelect: (decisionId: string) => void;
}) {
  const board = state.data?.workboard;

  return (
    <section className="workboard" aria-labelledby="workboard-title">
      <header className="section-heading section-heading--compact">
        <div>
          <p className="eyebrow">Currency-safe triage</p>
          <h2 id="workboard-title">What to work first</h2>
          <p>
            Start with the largest declared settlement values in each original currency. This is an
            operational queue, not a cash-at-risk claim.
          </p>
        </div>
        {board ? <span className="section-heading__meta">Rules {board.triage_version}</span> : null}
      </header>

      {state.loading ? <Loading what="work priorities" /> : null}
      {state.error ? (
        <ErrorNotice error={state.error} onRetry={state.reload} what="work priorities" />
      ) : null}

      {board ? (
        <>
          <p className="workboard__note">{board.prioritisation_note}</p>
          {board.currency_queues.length === 0 && board.unpriced_items.length === 0 ? (
            <EmptyState title="No unresolved work in this run">
              Every recorded settlement decision resolved. Later evidence would create a new run,
              never revise this one.
            </EmptyState>
          ) : null}
          {board.currency_queues.length > 0 ? (
            <div className="workboard__queues">
              {board.currency_queues.map((queue) => (
                <article className="workboard__queue" key={queue.currency}>
                  <header className="workboard__queue-head">
                    <h3>{queue.currency}</h3>
                    <span>{formatMinorUnits(queue.items.length)} open item(s)</span>
                  </header>
                  <div className="table-scroll">
                    <table>
                      <caption>
                        Open work ranked by absolute declared settlement net in {queue.currency}.
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">Rank</th>
                          <th scope="col">Settlement line</th>
                          <th scope="col" className="num">
                            Declared net
                          </th>
                          <th scope="col">Finding</th>
                        </tr>
                      </thead>
                      <tbody>
                        {queue.items.map((item) => (
                          <tr key={item.decision_id}>
                            <td className="num">{item.rank_in_currency}</td>
                            <td>
                              <a
                                className="row-button mono"
                                href="#decision-certificate"
                                onClick={() => {
                                  onSelect(item.decision_id);
                                }}
                              >
                                {item.subject_settlement_line_id}
                                <span className="visually-hidden"> — open its certificate</span>
                              </a>
                            </td>
                            <td className="num workboard__value">
                              {formatMinorUnits(item.declared_settlement_value.net_minor)}{' '}
                              {queue.currency}
                              <span>minor units</span>
                            </td>
                            <td>
                              {item.exception_codes.length > 0 ? (
                                <div className="chips">
                                  {item.exception_codes.map((code) => (
                                    <span className="chip chip--exception" key={code}>
                                      {code}
                                    </span>
                                  ))}
                                </div>
                              ) : (
                                <StatusBadge status={item.status} />
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </article>
              ))}
            </div>
          ) : null}
          {board.unpriced_items.length > 0 ? (
            <section className="workboard__unpriced" aria-labelledby="unpriced-work-title">
              <h3 id="unpriced-work-title">Needs evidence before it can be prioritised</h3>
              <p>
                These lines remain open, but the cited settlement fact cannot be safely re-read, so
                the workboard refuses to put a made-up amount beside them.
              </p>
              <ul>
                {board.unpriced_items.map((item) => (
                  <li key={item.decision_id}>
                    <a
                      className="row-button mono"
                      href="#decision-certificate"
                      onClick={() => {
                        onSelect(item.decision_id);
                      }}
                    >
                      {item.subject_settlement_line_id}
                      <span className="visually-hidden"> — open its certificate</span>
                    </a>{' '}
                    <span className="panel__note">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

/**
 * The bank finality panel for one run's snapshot.
 *
 * A separate panel rather than a column in the decisions table, because the two
 * are separate conclusions about different evidence and putting a finality
 * outcome beside a settlement status in one row is exactly the conflation this
 * phase exists to prevent. A payout is also not a settlement line: one payout
 * covers many lines, so there is no row to put it in.
 *
 * The audit is found by the run's own snapshot fingerprint. Both are computed
 * over the same accepted facts, so the join is exact rather than by time.
 */
export function BankFinalityPanel({ snapshotFingerprint }: { snapshotFingerprint: string }) {
  const [reloads, setReloads] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const found = useLoad(
    () => listBankFinalityAudits({ snapshot_fingerprint: snapshotFingerprint, limit: 1 }),
    `${snapshotFingerprint}|bank-finality|${String(reloads)}`,
  );
  const auditId = found.data?.audits[0]?.audit_id ?? null;

  const detail = useLoad(
    () => (auditId === null ? Promise.resolve(null) : getBankFinalityAudit(auditId)),
    `${auditId ?? 'none'}|bank-finality-detail`,
  );

  async function audit(): Promise<void> {
    setBusy(true);
    setFailure(null);
    try {
      await createBankFinalityAudit();
      setReloads((previous) => previous + 1);
    } catch (cause) {
      setFailure(cause);
    } finally {
      setBusy(false);
    }
  }

  const certificates = detail.data?.certificates ?? [];
  const summary = detail.data?.audit ?? null;

  return (
    <Panel
      title="Bank finality"
      note="Whether a bank statement shows each payout arriving. A separate conclusion from the settlement decisions above."
      actions={
        auditId === null ? (
          <button
            type="button"
            className="button button--quiet button--small"
            disabled={busy}
            onClick={() => void audit()}
          >
            {busy ? 'Auditing…' : 'Audit bank finality'}
          </button>
        ) : null
      }
    >
      {found.loading ? <Loading what="the bank finality audit" /> : null}
      {found.error ? (
        <ErrorNotice error={found.error} onRetry={found.reload} what="the bank finality audit" />
      ) : null}
      {failure === null ? null : (
        <p className="notice notice--error" role="alert">
          {describeError(failure)}
        </p>
      )}

      {!found.loading && !found.error && auditId === null ? (
        <EmptyState title="No bank finality audit for this snapshot yet">
          Nothing here has been checked against a bank statement. Until it is, this system makes no
          claim that any payout reached the merchant, whatever the settlement decisions above say.
        </EmptyState>
      ) : null}

      {detail.data && summary ? (
        <>
          <SeparateConclusionsNotice note={detail.data.settlement_and_finality_are_separate} />
          <Stats label="Bank finality summary">
            <Stat label="Payouts audited" value={formatMinorUnits(summary.payout_count)} />
            <Stat label="Statement rows" value={formatMinorUnits(summary.bank_transaction_count)} />
            <Stat
              label="Bank credits verified"
              value={formatMinorUnits(summary.verified_payout_count)}
            />
          </Stats>
          <p className="panel__note" style={{ marginTop: 12 }}>
            Audited {formatTimestamp(summary.created_at)} · rules {summary.bank_finality_version} ·
            statement schema {summary.bank_statement_schema_version}
          </p>
          <div className="certificates">
            {certificates.map((certificate) => (
              <BankFinalityCertificateView key={certificate.payout_id} certificate={certificate} />
            ))}
          </div>
        </>
      ) : null}
    </Panel>
  );
}
