import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  createBankFinalityAudit,
  createRun,
  evidenceRequestDownloadUrl,
  getRun,
  getWorkboard,
  listBankFinalityAudits,
  listRuns,
} from '../api/client';
import type { DecisionView, RunSummary, WorkboardItem } from '../api/types';
import { describeError } from '../api/errors';
import { useLoad } from '../hooks';
import { formatTimestamp, humanise, shortHash } from '../format';
import {
  downloadText,
  evidenceRequestText,
  findingTitle,
  money,
  prepareSampleWorkspace,
  SAMPLE_SOURCES,
  TEAM_LABELS,
} from '../workspace';
import { Icon } from '../components/Icon';
import { FocusPanel } from '../components/FocusPanel';
import { DecisionCertificate } from '../components/DecisionCertificate';
import { ErrorNotice, StatusBadge } from '../components/ui';

const PAGE_SIZE = 8;

export function DeskPage() {
  const [params, setParams] = useSearchParams();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState('');
  const busyRef = useRef(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState<unknown>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('open');
  const [currency, setCurrency] = useState('');
  const [page, setPage] = useState(0);
  const onlyIssues = params.get('view') === 'issues';
  const chosenRun = params.get('run') ?? '';

  const state = useLoad(
    async () => {
      const history = await listRuns({ limit: 1 });
      const runId = chosenRun || history.runs[0]?.run_id;
      if (!runId) return null;
      const [detail, workboard] = await Promise.all([getRun(runId), getWorkboard(runId)]);
      // A bank query may fail independently; its absence never becomes "zero credits".
      const bank = await listBankFinalityAudits({
        limit: 1,
        snapshot_fingerprint: detail.run.snapshot_fingerprint,
      })
        .then((result) => ({ audit: result.audits[0] ?? null, unavailable: false }))
        .catch(() => ({ audit: null, unavailable: true }));
      return { detail, workboard: workboard.workboard, bank };
    },
    `${chosenRun}|${String(revision)}`,
  );

  async function execute(kind: 'sample' | 'reconcile' | 'bank') {
    if (busyRef.current) return;
    busyRef.current = true;
    setFailure(null);
    setNotice('');
    setBusy(
      kind === 'sample'
        ? 'Preparing sample workspace…'
        : kind === 'bank'
          ? 'Checking bank credits…'
          : 'Reconciling records…',
    );
    try {
      if (kind === 'bank') {
        const { audit } = await createBankFinalityAudit();
        const sameSnapshot =
          state.data?.detail.run.snapshot_fingerprint === audit.snapshot_fingerprint;
        setNotice(
          sameSnapshot
            ? `Bank check complete: ${String(audit.verified_payout_count)} of ${String(audit.payout_count)} payouts have a verified credit.`
            : 'Bank check saved for newer evidence. Reconcile again to view that evidence on this desk.',
        );
      } else {
        const run =
          kind === 'sample' ? await prepareSampleWorkspace(setBusy) : (await createRun()).run;
        setParams({ ...(onlyIssues ? { view: 'issues' } : {}), run: run.run_id });
        setPage(0);
        setNotice(
          kind === 'sample'
            ? 'Sample sources imported. Your working batch is ready.'
            : 'Reconciliation complete. You are viewing the recorded result.',
        );
        if (kind === 'sample') {
          setBusy('Checking the bank statement…');
          try {
            await createBankFinalityAudit();
          } catch {
            setNotice(
              'Your batch is ready. The bank check did not finish; retry it from the Bank credits card.',
            );
          }
        }
      }
    } catch (cause) {
      setFailure(cause);
    } finally {
      busyRef.current = false;
      setBusy('');
      setRevision((value) => value + 1);
    }
  }

  const workspace = state.data;
  const run = workspace?.detail.run;
  const decisions = workspace?.detail.decisions ?? [];
  const amounts = new Map<string, WorkboardItem>();
  for (const queue of workspace?.workboard.currency_queues ?? []) {
    for (const item of queue.items) amounts.set(item.decision_id, item);
  }
  const resolved = run?.status_counts.RESOLVED ?? 0;
  const open = run ? run.decision_count - resolved : 0;
  const selected = decisions.find((decision) => decision.decision_id === params.get('case'));
  const visible = decisions
    .filter((decision) => {
      const matchesStatus =
        filter === 'all' ||
        (filter === 'open' ? decision.status !== 'RESOLVED' : decision.status === filter);
      const matchesCurrency =
        !currency ||
        amounts.get(decision.decision_id)?.declared_settlement_value.currency === currency;
      const haystack = [
        decision.subject_settlement_line_id,
        findingTitle(decision),
        TEAM_LABELS[decision.closure_plan.primary_owner],
        ...decision.exception_codes,
      ]
        .join(' ')
        .toLowerCase();
      return matchesStatus && matchesCurrency && haystack.includes(search.toLowerCase());
    })
    .sort((a, b) => {
      const first = amounts.get(a.decision_id);
      const second = amounts.get(b.decision_id);
      if (first && second) {
        const byCurrency = first.declared_settlement_value.currency.localeCompare(
          second.declared_settlement_value.currency,
        );
        return byCurrency || first.rank_in_currency - second.rank_in_currency;
      }
      return first
        ? -1
        : second
          ? 1
          : a.subject_settlement_line_id.localeCompare(b.subject_settlement_line_id);
    });
  const currentPage = Math.min(page, Math.max(0, Math.ceil(visible.length / PAGE_SIZE) - 1));
  const slice = visible.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  function choose(decisionId: string | null) {
    const next = new URLSearchParams(params);
    if (decisionId) next.set('case', decisionId);
    else next.delete('case');
    setParams(next, { replace: true });
  }

  function exportBrief() {
    if (!run) return;
    downloadText(
      'settlement-brief.txt',
      [
        'SETTLEMENT WITNESS · BATCH BRIEF',
        `Run: ${run.run_id}`,
        `As of: ${run.as_of}`,
        `Snapshot: ${run.snapshot_fingerprint}`,
        '',
        `Settlement lines: ${String(run.decision_count)}`,
        `Matched: ${String(resolved)}`,
        `Unresolved: ${String(open)}`,
        workspace.bank.audit
          ? `Verified bank credits: ${String(workspace.bank.audit.verified_payout_count)} / ${String(workspace.bank.audit.payout_count)}`
          : 'Bank credits: not available for this snapshot',
        '',
        'OPEN WORK',
        ...decisions
          .filter((d) => d.status !== 'RESOLVED')
          .flatMap((d) => {
            const value = amounts.get(d.decision_id)?.declared_settlement_value;
            return [
              `${d.subject_settlement_line_id} · ${findingTitle(d)}`,
              value
                ? `Declared settlement net: ${money(value.net_minor, value.currency)}`
                : 'Amount: not verified',
              `Team: ${TEAM_LABELS[d.closure_plan.primary_owner]}`,
              ...d.closure_plan.actions.map((action) => `Next: ${action.instruction}`),
              '',
            ];
          }),
        'Amounts describe individual settlement lines. They are not a loss estimate. Currency totals are not combined.',
        'Shared synthetic workspace. Match rate is an operational outcome, not production accuracy.',
      ].join('\n'),
    );
  }

  return (
    <>
      <header className="desk-heading">
        <div>
          <p className="desk-kicker">FINANCE OPERATIONS</p>
          <h1>{onlyIssues ? 'Your attention, where it matters.' : 'Settlement desk'}</h1>
          <p>
            {onlyIssues
              ? 'Every open item has a next step. Start with the evidence.'
              : 'Know what matched, what reached the bank, and what needs you.'}
          </p>
        </div>
        <div className="desk-heading__actions">
          {run ? (
            <button className="button button--quiet" onClick={exportBrief}>
              <Icon name="download" /> Export brief
            </button>
          ) : null}
          <Link className="button" to="/imports">
            <Icon name="plus" /> Add records
          </Link>
        </div>
      </header>
      {busy ? (
        <div className="desk-progress" role="status">
          <span className="spinner" />
          {busy}
          <span>Each completed step is saved.</span>
        </div>
      ) : null}
      {notice ? (
        <p className="desk-feedback" role="status">
          <Icon name="check" />
          {notice}
        </p>
      ) : null}
      {failure ? (
        <div className="notice notice--error" role="alert">
          {describeError(failure)} <Link to="/imports">View import receipts</Link>
        </div>
      ) : null}
      {state.error ? (
        <ErrorNotice what="your workspace" error={state.error} onRetry={state.reload} />
      ) : null}
      {state.loading ? (
        <div className="desk-skeleton" role="status" aria-label="Loading settlement desk">
          <div />
          <div />
          <div />
          <div />
        </div>
      ) : null}
      {!state.loading && !state.error && !workspace ? (
        <section className="desk-welcome">
          <div className="desk-welcome__copy">
            <span className="welcome-label">
              <span /> YOUR WORKSPACE IS READY
            </span>
            <h2>
              A clear answer to
              <br />
              <em>“where’s the money?”</em>
            </h2>
            <p>
              Bring payments, settlements and bank records into one place. We’ll check the batch and
              turn every loose end into a concrete next step.
            </p>
            <button
              className="button"
              disabled={!!busy}
              onClick={() => {
                void execute('sample');
              }}
            >
              Explore a sample business <Icon name="arrow" />
            </button>
            <small>
              Loads 236 synthetic records into this shared workspace.
              <br />
              Existing sample records are safely reused.
            </small>
            <button
              className="text-button"
              disabled={!!busy}
              onClick={() => {
                void execute('reconcile');
              }}
            >
              Already added records? Reconcile them <Icon name="arrow" size={15} />
            </button>
          </div>
          <div className="welcome-ledger" aria-label="Sample sources">
            <header>
              <span className="brand-mark brand-mark--small">w</span>
              <div>
                <strong>One batch. The whole picture.</strong>
                <small>Payments → settlement → bank</small>
              </div>
            </header>
            {SAMPLE_SOURCES.map((source, index) => (
              <div className="welcome-ledger__row" key={source.file}>
                <span className="source-icon">
                  <Icon name={index === 3 ? 'bank' : 'file'} />
                </span>
                <div>
                  <strong>{source.label}</strong>
                  <small>{source.rows} sample records</small>
                </div>
                <span className="source-ready">Ready to import</span>
              </div>
            ))}
            <footer>
              <Icon name="shield" />
              <span>Every finding links back to its source.</span>
            </footer>
          </div>
        </section>
      ) : null}
      {!state.loading && workspace && run ? (
        <>
          <div className="batch-toolbar">
            <span>
              <span className="live-dot" /> Recorded batch{' '}
              <strong>{shortHash(run.run_id, 8)}</strong>
              <span className="batch-time">{formatTimestamp(run.created_at)}</span>
            </span>
            <button
              className="text-button"
              disabled={!!busy}
              onClick={() => {
                void execute('reconcile');
              }}
            >
              <Icon name="refresh" size={16} /> Reconcile latest records
            </button>
          </div>
          <section className="desk-metrics" aria-label="Batch overview">
            <div className="desk-metric">
              <span>
                Settlement lines <Icon name="file" />
              </span>
              <strong>{run.decision_count}</strong>
              <small>Across {run.fact_count} source records</small>
            </div>
            <div className="desk-metric">
              <span>
                Automatically matched <Icon name="check" />
              </span>
              <strong>
                {resolved}
                <small className="metric-fraction"> / {run.decision_count}</small>
              </strong>
              <small>
                {run.decision_count
                  ? `${((resolved / run.decision_count) * 100).toFixed(1)}% match rate`
                  : 'No settlement lines to match'}
              </small>
            </div>
            <div className="desk-metric">
              <span>
                Need attention <Icon name="inbox" />
              </span>
              <strong className={open ? 'metric-amber' : ''}>{open}</strong>
              <small>{run.status_counts.INSUFFICIENT_EVIDENCE ?? 0} need supporting records</small>
            </div>
            <div className="desk-metric">
              <span>
                Verified bank credits <Icon name="bank" />
              </span>
              <strong>
                {workspace.bank.audit ? workspace.bank.audit.verified_payout_count : '—'}
                {workspace.bank.audit ? (
                  <small className="metric-fraction"> / {workspace.bank.audit.payout_count}</small>
                ) : null}
              </strong>
              <small>
                {workspace.bank.unavailable
                  ? 'Bank check unavailable'
                  : workspace.bank.audit
                    ? 'Payouts backed by a statement credit'
                    : 'Bank check has not run for this batch'}
              </small>
            </div>
          </section>
          {!onlyIssues ? (
            <div className="desk-briefing">
              <section className="close-progress">
                <div>
                  <p className="desk-kicker">BATCH HEALTH</p>
                  <h2>
                    {open === 0
                      ? 'All settlement lines matched.'
                      : `${String(open)} loose ends. A next step for each.`}
                  </h2>
                  <p>
                    {open === 0
                      ? 'Check the bank statement to confirm the corresponding credits.'
                      : 'Start with the largest declared settlement in each currency. Open a case for the records and the action it needs.'}
                  </p>
                </div>
                <div
                  className="close-progress__bar"
                  aria-label={`${String(resolved)} matched, ${String(open)} unresolved`}
                >
                  <span
                    style={{
                      width: `${String(run.decision_count ? (resolved / run.decision_count) * 100 : 0)}%`,
                    }}
                  />
                </div>
                <div className="close-progress__legend">
                  <span>
                    <i />
                    Matched {resolved}
                  </span>
                  <span>
                    <i />
                    Unresolved {open}
                  </span>
                  <a href="#attention">
                    View open work <Icon name="arrow" size={15} />
                  </a>
                </div>
              </section>
              <section className="bank-check-card">
                <span className="source-icon">
                  <Icon name="bank" />
                </span>
                <div>
                  <h2>Did the money reach the bank?</h2>
                  <p>
                    Check each payout’s reference, amount and currency against the bank statement.
                  </p>
                  <button
                    className="text-button"
                    disabled={!!busy}
                    onClick={() => {
                      void execute('bank');
                    }}
                  >
                    {workspace.bank.audit ? 'Refresh bank check' : 'Check bank credits'}{' '}
                    <Icon name="arrow" size={16} />
                  </button>
                </div>
              </section>
            </div>
          ) : null}
          <div className={`desk-inbox-layout${selected ? ' has-case' : ''}`}>
            <section className="desk-inbox" id="attention" aria-labelledby="inbox-title">
              <header className="inbox-heading">
                <div>
                  <h2 id="inbox-title">
                    Attention inbox <span>{open}</span>
                  </h2>
                  <p>Amounts are declared settlement nets, ordered within each currency.</p>
                </div>
                <Link to={`/runs/${run.run_id}/review`} className="text-button">
                  Review history <Icon name="arrow" size={15} />
                </Link>
              </header>
              <div className="inbox-tools">
                <div className="inbox-tabs" aria-label="Filter settlement lines">
                  {[
                    { value: 'open', label: 'Needs attention' },
                    { value: 'RESOLVED', label: 'Matched' },
                    { value: 'all', label: 'All lines' },
                  ].map((item) => (
                    <button
                      type="button"
                      key={item.value}
                      aria-pressed={filter === item.value}
                      onClick={() => {
                        setFilter(item.value);
                        setPage(0);
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <label className="desk-search">
                  <Icon name="search" size={17} />
                  <span className="visually-hidden">Search settlement lines</span>
                  <input
                    value={search}
                    placeholder="Find a line or finding…"
                    onChange={(e) => {
                      setSearch(e.target.value);
                      setPage(0);
                    }}
                  />
                </label>
              </div>
              {workspace.workboard.currency_queues.length > 1 || currency ? (
                <div className="currency-filter">
                  <label htmlFor="desk-currency">Currency</label>
                  <select
                    id="desk-currency"
                    value={currency}
                    onChange={(e) => {
                      setCurrency(e.target.value);
                      setPage(0);
                    }}
                  >
                    <option value="">All currencies (kept separate)</option>
                    {workspace.workboard.currency_queues.map((q) => (
                      <option key={q.currency}>{q.currency}</option>
                    ))}
                  </select>
                </div>
              ) : null}
              <div className="table-scroll">
                <table className="inbox-table">
                  <thead>
                    <tr>
                      <th>Settlement / finding</th>
                      <th>Team to involve</th>
                      <th className="num">Settlement net</th>
                      <th>
                        <span className="visually-hidden">Action</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {slice.map((decision) => {
                      const item = amounts.get(decision.decision_id);
                      const value = item?.declared_settlement_value;
                      return (
                        <tr
                          key={decision.decision_id}
                          className={
                            selected?.decision_id === decision.decision_id ? 'is-selected' : ''
                          }
                        >
                          <td>
                            <button
                              className="case-link"
                              onClick={() => {
                                choose(decision.decision_id);
                              }}
                            >
                              {findingTitle(decision)}
                            </button>
                            <span className="line-subtitle">
                              {decision.subject_settlement_line_id}
                              {decision.exception_codes.length > 1
                                ? ` · +${String(decision.exception_codes.length - 1)} findings`
                                : ''}
                            </span>
                          </td>
                          <td>
                            <span className="team-label">
                              <i />
                              {TEAM_LABELS[decision.closure_plan.primary_owner]}
                            </span>
                          </td>
                          <td className="num">
                            <strong>{value ? money(value.net_minor, value.currency) : '—'}</strong>
                            <small>
                              {value
                                ? `Priority ${String(item.rank_in_currency)} · ${value.currency}`
                                : decision.status === 'RESOLVED'
                                  ? 'Matched line'
                                  : 'Amount not verified'}
                            </small>
                          </td>
                          <td>
                            <button
                              className="icon-button"
                              aria-label={`Open ${decision.subject_settlement_line_id}`}
                              onClick={() => {
                                choose(decision.decision_id);
                              }}
                            >
                              <Icon name="chevron" size={17} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {visible.length === 0 ? (
                <div className="desk-empty">
                  <Icon name="check" />
                  <h3>
                    {search || currency
                      ? 'No lines match these filters.'
                      : 'No lines in this view.'}
                  </h3>
                  <p>Choose another view or clear your search.</p>
                </div>
              ) : null}
              <footer className="inbox-footer">
                <span>
                  {visible.length === 0
                    ? '0'
                    : `${String(currentPage * PAGE_SIZE + 1)}–${String(Math.min((currentPage + 1) * PAGE_SIZE, visible.length))}`}{' '}
                  of {visible.length} lines
                </span>
                <nav aria-label="Inbox pages">
                  <button
                    className="button button--quiet button--small"
                    disabled={currentPage === 0}
                    onClick={() => {
                      setPage(currentPage - 1);
                    }}
                  >
                    Previous
                  </button>
                  <button
                    className="button button--quiet button--small"
                    disabled={(currentPage + 1) * PAGE_SIZE >= visible.length}
                    onClick={() => {
                      setPage(currentPage + 1);
                    }}
                  >
                    Next
                  </button>
                </nav>
              </footer>
            </section>
            {selected ? (
              <CasePanel
                key={selected.decision_id}
                run={run}
                decision={selected}
                onClose={() => {
                  choose(null);
                }}
              />
            ) : null}
          </div>
          <div className="desk-footnote">
            <span>
              <Icon name="shield" size={16} /> Matched records and bank credits are checked
              separately.
            </span>
            <Link to="/benchmark">
              See how the checks are measured <Icon name="arrow" size={14} />
            </Link>
          </div>
        </>
      ) : null}
      {!workspace && !state.loading ? (
        <section className="desk-start-links">
          <Link to="/imports">
            <Icon name="file" />
            <div>
              <strong>Bring your own sample data</strong>
              <span>Upload CSVs and track every import.</span>
            </div>
            <Icon name="arrow" />
          </Link>
          <Link to="/benchmark">
            <Icon name="chart" />
            <div>
              <strong>Inspect the benchmark</strong>
              <span>59 scenarios. Measured outcomes and every exception.</span>
            </div>
            <Icon name="arrow" />
          </Link>
        </section>
      ) : null}
    </>
  );
}

function CasePanel({
  run,
  decision,
  onClose,
}: {
  run: RunSummary;
  decision: DecisionView;
  onClose: () => void;
}) {
  const plan = decision.closure_plan;
  return (
    <FocusPanel label="Case details">
      <header>
        <span className="desk-kicker">CASE DETAILS</span>
        <button className="icon-button" aria-label="Close case details" onClick={onClose}>
          <Icon name="close" />
        </button>
      </header>
      <StatusBadge status={decision.status} />
      <h2>{findingTitle(decision)}</h2>
      <p className="line-subtitle">{decision.subject_settlement_line_id}</p>
      <div className="case-owner">
        <span>Team to involve</span>
        <strong>{TEAM_LABELS[plan.primary_owner]}</strong>
      </div>
      <h3>{plan.actions.length ? 'Next steps' : 'No follow-up needed'}</h3>
      <ol className="case-steps">
        {plan.actions.map((action, index) => (
          <li key={action.action_code}>
            <span>{index + 1}</span>
            <div>
              <strong>{action.title}</strong>
              <p>{action.instruction}</p>
              <details>
                <summary>Records needed</summary>
                <p>{action.evidence_required}</p>
              </details>
              {!action.supported_by_current_contract ? (
                <small>Requires a new evidence rule before it can be verified.</small>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {decision.status !== 'RESOLVED' ? (
        <div className="case-actions">
          <button
            className="button"
            onClick={() => {
              downloadText('evidence-request.txt', evidenceRequestText(run, decision));
            }}
          >
            <Icon name="download" size={17} /> Download evidence request
          </button>
          <Link
            className="button button--quiet"
            to={`/runs/${run.run_id}/review?decision=${encodeURIComponent(decision.decision_id)}`}
          >
            Record a follow-up <Icon name="arrow" size={16} />
          </Link>
          <p>Download prepares a request for you to share. It does not contact the team.</p>
        </div>
      ) : null}
      <details className="case-proof">
        <summary>
          View checks & source evidence <Icon name="shield" size={16} />
        </summary>
        <DecisionCertificate
          decision={decision}
          evidenceRequestHref={evidenceRequestDownloadUrl(run.run_id, decision.decision_id)}
        />
      </details>
      <p className="case-gate">
        {plan.requires_new_run
          ? 'New evidence creates a new result. This decision stays in the audit history.'
          : 'This result is recorded with its supporting evidence.'}
      </p>
      {decision.exception_codes.length > 1 ? (
        <small>All findings: {decision.exception_codes.map(humanise).join(' · ')}</small>
      ) : null}
    </FocusPanel>
  );
}
