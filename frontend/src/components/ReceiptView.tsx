/**
 * One import receipt, shown as what it is: the record of an attempt.
 *
 * A rejected import is not a failed request. It produced a receipt, that
 * receipt is stored, and the whole audit trail depends on it existing. So this
 * shows the outcome plainly and says in words whether facts were written,
 * rather than reporting "upload succeeded" and leaving a reader to work out
 * that nothing was stored.
 *
 * Row outcomes carry the column and the rule a row broke. They never carry the
 * cell that broke it, because the API does not return it and this screen has no
 * business displaying document content.
 */

import { formatMinorUnits, formatTimestamp } from '../format';
import type { ImportReceipt } from '../api/types';
import { Facts, OutcomeBadge } from './ui';

const WROTE_NOTHING =
  'No facts were written. The receipt below is still stored, because a refused document is part of the audit trail.';

const OUTCOME_EXPLANATION: Record<string, string> = {
  ACCEPTED: 'Every row was new and all of them were stored.',
  DUPLICATE_NO_OP:
    'Every row was already stored with the same payload, so nothing changed. Re-importing a document is safe and this is the correct result, not an error.',
  REJECTED_INVALID: `At least one row could not be read, so the whole document was refused. ${WROTE_NOTHING}`,
  REJECTED_CONFLICT: `At least one row contradicts a fact already stored, so the whole document was refused. ${WROTE_NOTHING}`,
};

const ROW_LABELS: Record<string, string> = {
  ACCEPTED: 'Stored',
  DUPLICATE_NO_OP: 'Already stored, identical',
  DUPLICATE_CONFLICT: 'Contradicts a stored fact',
  REJECTED: 'Could not be read',
  NOT_APPLIED: 'Readable, not stored',
};

export function ReceiptView({ receipt }: { receipt: ImportReceipt }) {
  const explanation = OUTCOME_EXPLANATION[receipt.outcome] ?? '';
  const problems = receipt.row_outcomes.filter(
    (row) => row.outcome === 'REJECTED' || row.outcome === 'DUPLICATE_CONFLICT',
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <OutcomeBadge outcome={receipt.outcome} />
        <span className="mono">{receipt.document_name}</span>
      </div>

      <p className="page__lede">{explanation}</p>

      <p>
        <strong>{receipt.wrote_facts ? 'Facts were written.' : 'No facts were written.'}</strong>{' '}
        {receipt.wrote_facts
          ? `${formatMinorUnits(receipt.accepted_count)} row(s) are now stored as source facts.`
          : 'The store is unchanged by this attempt.'}
      </p>

      <div className="stats" role="group" aria-label="Import summary">
        <div className="stat">
          <div className="stat__value">{formatMinorUnits(receipt.row_count)}</div>
          <div className="stat__label">Rows read</div>
        </div>
        <div className="stat stat--resolved">
          <div className="stat__value">{formatMinorUnits(receipt.accepted_count)}</div>
          <div className="stat__label">Stored</div>
        </div>
        <div className="stat">
          <div className="stat__value">{formatMinorUnits(receipt.duplicate_count)}</div>
          <div className="stat__label">Duplicates</div>
        </div>
        <div className="stat stat--exception">
          <div className="stat__value">{formatMinorUnits(receipt.conflict_count)}</div>
          <div className="stat__label">Conflicts</div>
        </div>
        <div className="stat stat--exception">
          <div className="stat__value">{formatMinorUnits(receipt.rejected_count)}</div>
          <div className="stat__label">Unreadable</div>
        </div>
        <div className="stat stat--unknown">
          <div className="stat__value">{formatMinorUnits(receipt.not_applied_count)}</div>
          <div className="stat__label">Not applied</div>
        </div>
      </div>

      {receipt.failure_detail ? (
        <div className="notice notice--warn">
          <p className="notice__title">What went wrong</p>
          <p className="notice__body">{receipt.failure_detail}</p>
        </div>
      ) : null}

      <Facts
        items={[
          ['Receipt ID', <span className="mono">{receipt.receipt_id}</span>],
          ['Declared source system', receipt.source_system],
          ['Read as record type', receipt.source_record_type],
          ['Parser version', receipt.parser_version],
          ['Received at', formatTimestamp(receipt.received_at)],
          ['Document hash', <span className="hash">{receipt.document_hash}</span>],
        ]}
      />

      {receipt.row_outcomes.length > 0 ? (
        <div className="table-scroll">
          <table>
            <caption>
              What happened to each row. Row 1 is the header, so the first data row is row 2.
              {problems.length > 0
                ? ` ${formatMinorUnits(problems.length)} row(s) below explain why the document was refused.`
                : ''}
            </caption>
            <thead>
              <tr>
                <th scope="col" className="num">
                  Row
                </th>
                <th scope="col">Outcome</th>
                <th scope="col">Code</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {receipt.row_outcomes.map((row) => (
                <tr key={row.row_number}>
                  <td className="num mono">{row.row_number}</td>
                  <td>{ROW_LABELS[row.outcome] ?? row.outcome}</td>
                  <td className="mono">{row.code ?? '—'}</td>
                  <td>{row.detail ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
