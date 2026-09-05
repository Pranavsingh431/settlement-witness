/**
 * Uploading evidence, and reading what the server made of it.
 *
 * Two rules shape this screen. The record type and the source system are
 * declared by the person uploading and are never guessed from the file: a
 * document read as the wrong record type fails loudly on its headers, and one
 * read as the wrong source system would import cleanly and be wrong. And the
 * result shown is the receipt the server returned, not a message this screen
 * composed, because the receipt is the thing that was actually recorded.
 */

import { useCallback, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { importDocument, listImports } from '../api/client';
import { describeError } from '../api/errors';
import { IMPORTABLE_RECORD_TYPES, SOURCE_SYSTEMS } from '../api/types';
import type { ImportReceipt } from '../api/types';
import { formatMinorUnits, formatTimestamp, humanise } from '../format';
import { useLoad } from '../hooks';
import { ReceiptView } from '../components/ReceiptView';
import { EmptyState, ErrorNotice, Loading, OutcomeBadge } from '../components/ui';

const PAGE_SIZE = 10;
const TYPE_LABELS: Record<string, string> = {
  PAYMENT_EVENT: 'Payments & refunds',
  SETTLEMENT_LINE: 'Settlement lines',
  PAYOUT: 'Payouts',
  BANK_TRANSACTION: 'Bank transactions',
};
const SOURCE_LABELS: Record<string, string> = {
  PSP_API: 'Payment provider · API export',
  PSP_WEBHOOK: 'Payment provider · webhook',
  BANK_STATEMENT: 'Bank statement',
  MERCHANT_LEDGER: 'Merchant ledger',
};

const SAMPLE_FILES = [
  {
    type: 'PAYMENT_EVENT',
    source: 'PSP_API',
    file: 'payment_events.csv',
    rows: 65,
    what: 'captures, refunds and chargebacks',
  },
  {
    type: 'SETTLEMENT_LINE',
    source: 'PSP_API',
    file: 'settlement_lines.csv',
    rows: 59,
    what: 'what the provider settled',
  },
  {
    type: 'PAYOUT',
    source: 'PSP_API',
    file: 'payouts.csv',
    rows: 56,
    what: 'what was paid out, and the UTR where there is one',
  },
  {
    type: 'BANK_TRANSACTION',
    source: 'BANK_STATEMENT',
    file: 'bank_transactions.csv',
    rows: 56,
    what: 'bank credits that independently support payout finality',
  },
] as const;

export function ImportsPage() {
  const fileId = useId();
  const systemId = useId();
  const typeId = useId();
  const fileInput = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [sourceSystem, setSourceSystem] = useState<string>('PSP_API');
  const [recordType, setRecordType] = useState<string>('PAYMENT_EVENT');
  const [sampleReady, setSampleReady] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [receipt, setReceipt] = useState<ImportReceipt | null>(null);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [dragging, setDragging] = useState(false);
  const [page, setPage] = useState(0);
  const [outcome, setOutcome] = useState('');
  const [system, setSystem] = useState('');
  const [type, setType] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const history = useLoad(
    () =>
      listImports({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        outcome: outcome || undefined,
        source_system: system || undefined,
        record_type: type || undefined,
      }),
    `${String(page)}|${outcome}|${system}|${type}|${String(reloadKey)}`,
  );

  const submit = useCallback(
    async (event: { preventDefault: () => void }) => {
      event.preventDefault();
      if (file === null || uploading) {
        return;
      }
      setUploading(true);
      setUploadError(null);
      setReceipt(null);
      try {
        const result = await importDocument(file, sourceSystem, recordType);
        setReceipt(result);
        setPage(0);
        setReloadKey((previous) => previous + 1);
      } catch (cause) {
        setUploadError(cause);
      } finally {
        setUploading(false);
      }
    },
    [file, sourceSystem, recordType, uploading],
  );

  const chooseFile = useCallback((chosen: File | null) => {
    setFile(chosen);
    setUploadError(null);
  }, []);

  const total = history.data?.total ?? 0;
  const filtered = history.data?.filtered ?? false;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  const namedSample = SAMPLE_FILES.find((sample) => sample.file === file?.name);
  const suggestSample =
    namedSample && (namedSample.type !== recordType || namedSample.source !== sourceSystem);

  return (
    <>
      <section
        className="operations-hero operations-hero--evidence"
        aria-labelledby="evidence-title"
      >
        <div>
          <p className="eyebrow">Your records</p>
          <h1 id="evidence-title">Data sources</h1>
          <p>
            Add payment, settlement, payout and bank-statement files. Every import has a receipt, so
            you can see exactly what was added and what needs fixing.
          </p>
        </div>
        <ol className="operations-path" aria-label="Evidence intake steps">
          <li>
            <span>01</span>
            <div>
              <strong>Declare the source</strong>
              <small>System and record type are never guessed.</small>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <strong>Read the receipt</strong>
              <small>Accepted, replayed or refused—every attempt is explicit.</small>
            </div>
          </li>
        </ol>
      </section>

      <div className="source-next-step">
        <p>
          <strong>Want to explore first?</strong> Load all four sample files together from the
          settlement desk.
        </p>
        <Link className="button button--quiet button--small" to="/">
          Open settlement desk →
        </Link>
      </div>

      <section className="evidence-intake" aria-labelledby="upload-title">
        <header className="section-heading">
          <div>
            <p className="eyebrow">Step 1 · source document</p>
            <h2 id="upload-title">Add one CSV document</h2>
            <p>
              Download the seeded sample sources, then import them in order. Use only synthetic data
              in this public workspace.
            </p>
          </div>
          <span className="section-heading__meta">Receipt recorded on every attempt</span>
        </header>
        <div className="evidence-intake__body">
          <form
            noValidate
            onSubmit={(event) => {
              // The handler is async and the DOM wants nothing back, so the
              // promise is discarded on purpose rather than by omission.
              void submit(event);
            }}
          >
            <div
              className={`dropzone${dragging ? ' is-over' : ''}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => {
                setDragging(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                chooseFile(event.dataTransfer.files[0] ?? null);
              }}
            >
              <div className="field">
                <label className="field__label" htmlFor={fileId}>
                  CSV document
                </label>
                <input
                  id={fileId}
                  ref={fileInput}
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) => {
                    chooseFile(event.target.files?.[0] ?? null);
                  }}
                />
                <p className="field__hint">
                  Drop a file here or choose one. The server checks the headers against the record
                  type you declare below; nothing is guessed from the file name.
                </p>
              </div>
            </div>

            {suggestSample ? (
              <div className="sample-suggestion" role="note">
                <p>
                  <strong>Using our {TYPE_LABELS[namedSample.type]?.toLowerCase()} sample?</strong>{' '}
                  This filename matches it, but your selected settings differ. Use the preset only
                  if this is the downloaded sample.
                </p>
                <button
                  type="button"
                  className="button button--quiet button--small"
                  onClick={() => {
                    setSourceSystem(namedSample.source);
                    setRecordType(namedSample.type);
                    setSampleReady(namedSample.file);
                  }}
                >
                  Use this sample’s settings
                </button>
              </div>
            ) : null}

            <div className="form-row evidence-intake__fields">
              <div className="field">
                <label className="field__label" htmlFor={systemId}>
                  Declared source system
                </label>
                <select
                  id={systemId}
                  value={sourceSystem}
                  onChange={(event) => {
                    setSourceSystem(event.target.value);
                    setSampleReady(null);
                  }}
                >
                  {SOURCE_SYSTEMS.map((value) => (
                    <option key={value} value={value}>
                      {SOURCE_LABELS[value]}
                    </option>
                  ))}
                </select>
                <p className="field__hint">
                  Where the document came from. Never inferred: a file read as the wrong system
                  would import cleanly and be wrong.
                </p>
              </div>

              <div className="field">
                <label className="field__label" htmlFor={typeId}>
                  Declared record type
                </label>
                <select
                  id={typeId}
                  value={recordType}
                  onChange={(event) => {
                    setRecordType(event.target.value);
                    setSampleReady(null);
                  }}
                >
                  {IMPORTABLE_RECORD_TYPES.map((value) => (
                    <option key={value} value={value}>
                      {TYPE_LABELS[value]}
                    </option>
                  ))}
                </select>
                <p className="field__hint">
                  Which schema to read it as. Never inferred from the headers.
                </p>
              </div>
            </div>

            <div className="toolbar evidence-intake__actions">
              <button type="submit" className="button" disabled={file === null || uploading}>
                {uploading ? 'Importing…' : 'Import document'}
              </button>
              {file ? (
                <span className="panel__note">
                  <span className="mono">{file.name}</span> selected
                </span>
              ) : (
                <span className="panel__note">Choose a file to enable importing.</span>
              )}
            </div>
          </form>

          <aside className="source-guide" aria-label="Downloadable sample files">
            <p className="source-guide__eyebrow">Hands-on 59-case batch</p>
            <h3>Download, then import in this order</h3>
            <p className="source-guide__summary">
              180 provider records produce 59 decisions. First choose the matching{' '}
              <strong>Use source → type</strong> action for a sample; it fills the declaration
              without guessing from the file. The optional statement adds 56 matching bank credits
              for the separate cash-finality check.
            </p>
            <ol>
              {SAMPLE_FILES.map((row) => (
                <li key={row.type}>
                  <span className="source-guide__number" aria-hidden="true">
                    {SAMPLE_FILES.indexOf(row) + 1}
                  </span>
                  <div>
                    <div className="source-guide__file">
                      <strong className="mono">{row.file}</strong>
                      <a
                        className="sample-download"
                        href={`/samples/${row.file}`}
                        download={row.file}
                        aria-label={`Download ${row.file}`}
                      >
                        Download
                      </a>
                    </div>
                    <span>
                      <span className="mono">{row.source}</span> →{' '}
                      <span className="mono">{row.type}</span> · {row.rows} rows
                    </span>
                    <span>{row.what}</span>
                    <button
                      className="sample-configure"
                      type="button"
                      onClick={() => {
                        setSourceSystem(row.source);
                        setRecordType(row.type);
                        setSampleReady(row.file);
                        setUploadError(null);
                      }}
                    >
                      Use {row.source} → {row.type}
                    </button>
                  </div>
                </li>
              ))}
            </ol>
            <p className="sample-ready" role="status">
              {sampleReady === null ? (
                'Choose the matching “Use source → type” action, then select that CSV.'
              ) : (
                <>
                  Ready for <span className="mono">{sampleReady}</span>:{' '}
                  <span className="mono">{sourceSystem}</span> →{' '}
                  <span className="mono">{recordType}</span>. Now select that CSV.
                </>
              )}
            </p>
            <p className="source-guide__footnote">
              Re-importing an unchanged file correctly returns a duplicate no-op receipt.
            </p>
          </aside>
        </div>
      </section>

      <div aria-live="polite">
        {uploading ? (
          <div className="notice notice--info">
            <p className="notice__title">Importing…</p>
            <p className="notice__body">Waiting for the server to record a receipt.</p>
          </div>
        ) : null}
        {uploadError ? (
          <div className="notice notice--error" role="alert">
            <p className="notice__title">The document was not imported.</p>
            <p className="notice__body">{describeError(uploadError)}</p>
            <p className="notice__body">No receipt was written for this attempt.</p>
          </div>
        ) : null}
        {receipt ? (
          <section className="receipt-stage" aria-labelledby="receipt-title">
            <header className="section-heading section-heading--compact">
              <div>
                <p className="eyebrow">Step 2 · import receipt</p>
                <h2 id="receipt-title">What the server recorded</h2>
              </div>
              <span className="section-heading__meta">
                This is the audit record for this attempt
              </span>
            </header>
            <ReceiptView receipt={receipt} />
          </section>
        ) : null}
      </div>

      <section className="operations-ledger" aria-labelledby="import-history-title">
        <header className="section-heading">
          <div>
            <p className="eyebrow">Evidence ledger</p>
            <h2 id="import-history-title">Import history</h2>
            <p>Newest attempt first, in the order the attempts were made.</p>
          </div>
        </header>
        <div className="ledger-filters" aria-label="Import history filters">
          <div className="field">
            <label className="field__label" htmlFor="filter-outcome">
              Outcome
            </label>
            <select
              id="filter-outcome"
              value={outcome}
              onChange={(event) => {
                setOutcome(event.target.value);
                setPage(0);
              }}
            >
              <option value="">Any outcome</option>
              {['ACCEPTED', 'DUPLICATE_NO_OP', 'REJECTED_INVALID', 'REJECTED_CONFLICT'].map(
                (value) => (
                  <option key={value} value={value}>
                    {humanise(value)}
                  </option>
                ),
              )}
            </select>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="filter-system">
              Source system
            </label>
            <select
              id="filter-system"
              value={system}
              onChange={(event) => {
                setSystem(event.target.value);
                setPage(0);
              }}
            >
              <option value="">Any system</option>
              {SOURCE_SYSTEMS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="filter-type">
              Record type
            </label>
            <select
              id="filter-type"
              value={type}
              onChange={(event) => {
                setType(event.target.value);
                setPage(0);
              }}
            >
              <option value="">Any type</option>
              {IMPORTABLE_RECORD_TYPES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>

        {history.loading ? <Loading what="import history" /> : null}
        {history.error ? (
          <ErrorNotice error={history.error} onRetry={history.reload} what="import history" />
        ) : null}

        {history.data?.receipts.length === 0 ? (
          <EmptyState title={filtered ? 'No attempt matches these filters' : 'No imports yet'}>
            {filtered
              ? 'Widen the filters to see the rest of the history.'
              : 'Upload one of the four synthetic sample sources above to get started.'}
          </EmptyState>
        ) : null}

        {history.data && history.data.receipts.length > 0 ? (
          <>
            <div className="table-scroll">
              <table>
                <caption>
                  {filtered
                    ? `${formatMinorUnits(total)} attempt(s) match these filters. This is a filtered view, not the whole history.`
                    : `${formatMinorUnits(total)} attempt(s) in total.`}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    <th scope="col">System</th>
                    <th scope="col">Read as</th>
                    <th scope="col">Outcome</th>
                    <th scope="col" className="num">
                      Rows
                    </th>
                    <th scope="col" className="num">
                      Stored
                    </th>
                    <th scope="col">Received</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.receipts.map((row) => (
                    <tr key={row.receipt_id}>
                      <td className="mono">{row.document_name}</td>
                      <td>{row.source_system}</td>
                      <td>{row.source_record_type}</td>
                      <td>
                        <OutcomeBadge outcome={row.outcome} />
                      </td>
                      <td className="num">{formatMinorUnits(row.row_count)}</td>
                      <td className="num">{formatMinorUnits(row.accepted_count)}</td>
                      <td>{formatTimestamp(row.received_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar ledger-pagination">
              <button
                type="button"
                className="button button--quiet button--small"
                disabled={page === 0}
                onClick={() => {
                  setPage((previous) => Math.max(0, previous - 1));
                }}
              >
                Previous
              </button>
              <button
                type="button"
                className="button button--quiet button--small"
                disabled={page >= lastPage}
                onClick={() => {
                  setPage((previous) => previous + 1);
                }}
              >
                Next
              </button>
              <span className="panel__note">
                Page {page + 1} of {lastPage + 1}
              </span>
            </div>
          </>
        ) : null}
      </section>
    </>
  );
}
