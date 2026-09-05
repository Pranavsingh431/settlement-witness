import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  createBankFinalityAudit,
  getBankFinalityAudit,
  listBankFinalityAudits,
} from '../api/client';
import { describeError } from '../api/errors';
import { useLoad } from '../hooks';
import { money } from '../workspace';
import { Icon } from '../components/Icon';
import { FocusPanel } from '../components/FocusPanel';
import { BankFinalityCertificateView, FinalityBadge } from '../components/BankFinality';
import { ErrorNotice, Loading } from '../components/ui';

export function CashPage() {
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState('');
  const [filter, setFilter] = useState('all');
  const state = useLoad(
    async () => {
      const page = await listBankFinalityAudits({ limit: 1 });
      return page.audits[0] ? getBankFinalityAudit(page.audits[0].audit_id) : null;
    },
    `cash|${String(revision)}`,
  );
  async function check() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      await createBankFinalityAudit();
      setRevision((n) => n + 1);
    } catch (cause) {
      setError(cause);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  const data = state.data;
  const certificates =
    data?.certificates.filter(
      (c) =>
        filter === 'all' ||
        (filter === 'open'
          ? c.outcome !== 'VERIFIED_BANK_CREDIT'
          : c.outcome === 'VERIFIED_BANK_CREDIT'),
    ) ?? [];
  const certificate = data?.certificates.find((c) => c.payout_id === selected);
  return (
    <>
      <header className="desk-heading">
        <div>
          <p className="desk-kicker">CASH CONFIRMATION</p>
          <h1>Bank credits</h1>
          <p>See which payouts have a matching credit on the bank statement.</p>
        </div>
        <button
          className="button"
          disabled={busy}
          onClick={() => {
            void check();
          }}
        >
          <Icon name="refresh" />
          {busy ? 'Checking credits…' : 'Check bank credits'}
        </button>
      </header>
      {error ? (
        <p className="notice notice--error" role="alert">
          {describeError(error)}
        </p>
      ) : null}
      {state.loading ? <Loading what="bank credits" /> : null}
      {state.error ? (
        <ErrorNotice what="bank credits" error={state.error} onRetry={state.reload} />
      ) : null}
      {data ? (
        <>
          <section className="desk-metrics cash-metrics" aria-label="Bank credit summary">
            <div className="desk-metric">
              <span>
                Payouts checked <Icon name="bank" />
              </span>
              <strong>{data.audit.payout_count}</strong>
              <small>Against {data.audit.bank_transaction_count} statement records</small>
            </div>
            <div className="desk-metric">
              <span>
                Credit verified <Icon name="check" />
              </span>
              <strong>{data.audit.verified_payout_count}</strong>
              <small>Reference, direction, amount and currency agree</small>
            </div>
            <div className="desk-metric">
              <span>
                Need follow-up <Icon name="inbox" />
              </span>
              <strong>{data.audit.payout_count - data.audit.verified_payout_count}</strong>
              <small>Missing references or differences to investigate</small>
            </div>
          </section>
          <div className={`desk-inbox-layout${certificate ? ' has-case' : ''}`}>
            <section className="desk-inbox">
              <header className="inbox-heading">
                <div>
                  <h2>Payouts & bank evidence</h2>
                  <p>A provider match alone does not confirm money arrived.</p>
                </div>
              </header>
              <div className="inbox-tabs" aria-label="Filter bank credits">
                {[
                  ['all', 'All payouts'],
                  ['open', 'Needs follow-up'],
                  ['verified', 'Verified credits'],
                ].map(([value = '', label]) => (
                  <button
                    key={value}
                    aria-pressed={filter === value}
                    onClick={() => {
                      setFilter(value);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="table-scroll">
                <table className="inbox-table">
                  <thead>
                    <tr>
                      <th>Payout</th>
                      <th>Bank reference</th>
                      <th className="num">Expected credit</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {certificates.map((c) => (
                      <tr
                        key={c.payout_id}
                        className={selected === c.payout_id ? 'is-selected' : ''}
                      >
                        <td>
                          <button
                            className="case-link"
                            onClick={() => {
                              setSelected(c.payout_id);
                            }}
                          >
                            {c.payout_id}
                          </button>
                        </td>
                        <td>{c.bank_reference ?? 'Reference missing'}</td>
                        <td className="num">
                          {c.expected_amount_minor !== null && c.expected_currency
                            ? money(c.expected_amount_minor, c.expected_currency)
                            : '—'}
                        </td>
                        <td>
                          <FinalityBadge outcome={c.outcome} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {certificates.length === 0 ? (
                <div className="desk-empty">No payouts in this view.</div>
              ) : null}
            </section>
            {certificate ? (
              <FocusPanel key={certificate.payout_id} label="Bank evidence">
                <header>
                  <h2>Bank evidence</h2>
                  <button
                    className="icon-button"
                    aria-label="Close bank evidence"
                    onClick={() => {
                      setSelected('');
                    }}
                  >
                    <Icon name="close" />
                  </button>
                </header>
                <BankFinalityCertificateView certificate={certificate} />
              </FocusPanel>
            ) : null}
          </div>
        </>
      ) : !state.loading && !state.error ? (
        <section className="desk-empty desk-empty--large">
          <span className="source-icon">
            <Icon name="bank" size={28} />
          </span>
          <h2>Follow the money all the way to the bank.</h2>
          <p>Add a payout file and bank statement, then check their references and amounts.</p>
          <Link className="button" to="/imports">
            Add bank records <Icon name="arrow" />
          </Link>
        </section>
      ) : null}
      <p className="desk-footnote">
        Bank checks are recorded separately from settlement results. Each check uses the records
        available at that time.
      </p>
    </>
  );
}
