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

import { importDocument, listImports } from '../api/client';
import { describeError } from '../api/errors';
import { IMPORTABLE_RECORD_TYPES, SOURCE_SYSTEMS } from '../api/types';
import type { ImportReceipt } from '../api/types';
import { formatMinorUnits, formatTimestamp, humanise } from '../format';
import { useLoad } from '../hooks';
import { ReceiptView } from '../components/ReceiptView';
import { EmptyState, ErrorNotice, Loading, OutcomeBadge, Panel } from '../components/ui';

const PAGE_SIZE = 10;

const EXPECTED_FILES = [
  { type: 'PAYMENT_EVENT', file: 'payment_events.csv', what: 'captures, refunds and chargebacks' },
  { type: 'SETTLEMENT_LINE', file: 'settlement_lines.csv', what: 'what the provider settled' },
  {
    type: 'PAYOUT',
    file: 'payouts.csv',
    what: 'what was paid out, and the UTR where there is one',
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

  return (
    <>
      <div className="page__head">
        <div>
          <h1>Import evidence</h1>
          <p className="page__lede">
            Every attempt leaves a receipt, whether the document was taken or refused. A refused
            document writes no facts and its receipt is kept, because what was tried is part of the
            record.
          </p>
        </div>
      </div>

      <Panel
        title="Upload a CSV document"
        note="The three demo documents live in data/fixtures/ingestion."
      >
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

          <div className="form-row" style={{ marginTop: 16 }}>
            <div className="field">
              <label className="field__label" htmlFor={systemId}>
                Declared source system
              </label>
              <select
                id={systemId}
                value={sourceSystem}
                onChange={(event) => {
                  setSourceSystem(event.target.value);
                }}
              >
                {SOURCE_SYSTEMS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              <p className="field__hint">
                Where the document came from. Never inferred: a file read as the wrong system would
                import cleanly and be wrong.
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
                }}
              >
                {IMPORTABLE_RECORD_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              <p className="field__hint">
                Which schema to read it as. Never inferred from the headers.
              </p>
            </div>
          </div>

          <div className="toolbar" style={{ marginTop: 16 }}>
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

        <table style={{ marginTop: 20 }}>
          <caption>The three documents this demo expects.</caption>
          <thead>
            <tr>
              <th scope="col">Record type</th>
              <th scope="col">File</th>
              <th scope="col">Holds</th>
            </tr>
          </thead>
          <tbody>
            {EXPECTED_FILES.map((row) => (
              <tr key={row.type}>
                <td className="mono">{row.type}</td>
                <td className="mono">{row.file}</td>
                <td>{row.what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

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
          <Panel title="Receipt" note="What the server recorded for this attempt.">
            <ReceiptView receipt={receipt} />
          </Panel>
        ) : null}
      </div>

      <Panel
        title="Import history"
        note="Newest attempt first, in the order the attempts were made."
      >
        <div className="toolbar" style={{ marginBottom: 16 }}>
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
              : 'Upload one of the three demo documents above to get started.'}
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
            <div className="toolbar" style={{ marginTop: 14 }}>
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
      </Panel>
    </>
  );
}
