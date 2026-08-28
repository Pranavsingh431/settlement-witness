/**
 * Whether a bank says a payout arrived, shown as its own conclusion.
 *
 * The point of this panel is that it must never be mistaken for a settlement
 * decision. Those two answer different questions from different evidence: one
 * asks whether the provider's own records agree, and the other asks whether a
 * bank statement shows money reaching the merchant. A line can be `RESOLVED`
 * with no bank evidence at all, and a merchant cares far more about the second.
 *
 * So nothing here reuses `StatusBadge`, nothing here renders a green tick, and
 * the word "resolved" does not appear. The badge is a different shape with a
 * different mark, and the sentence saying the two are separate sits above it
 * and is not dismissible.
 */

import { formatMinorUnits, formatTimestamp } from '../format';
import type { BankFinalityCertificate, EvidenceReference } from '../api/types';
import { FINALITY_LABELS, FINALITY_MARKS } from '../bankFinality';

/**
 * The badge for a bank finality outcome.
 *
 * Deliberately not `StatusBadge`, and deliberately not its palette. A reader
 * glancing at a screen must not be able to mistake "the bank showed the credit"
 * for "the provider's records agree", because they are different facts and only
 * one of them means the merchant has the money.
 */
export function FinalityBadge({ outcome }: { outcome: string }) {
  const known = FINALITY_LABELS[outcome];
  const tone = known?.tone ?? 'absent';
  return (
    <span className={`finality finality--${tone}`}>
      <span className="finality__mark" aria-hidden="true">
        {FINALITY_MARKS[tone] ?? '·'}
      </span>
      {known?.label ?? outcome}
    </span>
  );
}

/** The sentence that must appear wherever a finality outcome appears. */
export function SeparateConclusionsNotice({ note }: { note: string }) {
  return (
    <p className="notice notice--info separate-note" role="note">
      <strong>Settlement decision and bank finality are separate conclusions.</strong> {note}
    </p>
  );
}

function EvidenceRow({ reference }: { reference: EvidenceReference }) {
  return (
    <li className="cited">
      <span className="mono cited__id">{reference.source_record_id}</span>
      <span className="cited__system">{reference.source_system}</span>
      <span className="cited__outcome">{reference.verification_outcome ?? 'not checked'}</span>
      <span className="mono hash">{reference.payload_hash}</span>
    </li>
  );
}

/**
 * One payout's certificate: what was compared, and against which records.
 *
 * The expected and observed values are shown side by side whenever there was a
 * single row to compare against. A certificate that reported only the outcome
 * would be asking to be believed; this one can be checked by anybody holding
 * the same statement.
 */
export function BankFinalityCertificateView({
  certificate,
}: {
  certificate: BankFinalityCertificate;
}) {
  const known = FINALITY_LABELS[certificate.outcome];
  const compared = certificate.observed_amount_minor !== null;
  return (
    <div className="certificate">
      <div className="certificate__head">
        <span className="mono">{certificate.payout_id}</span>
        <FinalityBadge outcome={certificate.outcome} />
      </div>
      <p className="certificate__what">{known?.what ?? certificate.outcome}</p>

      <dl className="facts">
        <div>
          <dt className="facts__key">Bank reference</dt>
          <dd className="facts__value mono">
            {certificate.bank_reference ?? 'none on the payout record'}
          </dd>
        </div>
        <div>
          <dt className="facts__key">Statement rows carrying it</dt>
          <dd className="facts__value mono">
            {certificate.matched_bank_transaction_ids.length === 0
              ? 'none'
              : certificate.matched_bank_transaction_ids.join(', ')}
          </dd>
        </div>
        <div>
          <dt className="facts__key">Audited</dt>
          <dd className="facts__value">{formatTimestamp(certificate.recorded_at)}</dd>
        </div>
      </dl>

      {compared ? (
        <div className="amounts amounts--finality">
          <div>
            <span className="amounts__label">Payout says</span>
            <span className="amounts__value mono">
              {formatMinorUnits(certificate.expected_amount_minor ?? 0)}{' '}
              {certificate.expected_currency}
            </span>
          </div>
          <div>
            <span className="amounts__label">Bank says</span>
            <span className="amounts__value mono">
              {formatMinorUnits(certificate.observed_amount_minor)} {certificate.observed_currency}{' '}
              {certificate.observed_direction}
            </span>
          </div>
          <div>
            <span className="amounts__label">Unit</span>
            <span className="amounts__value">minor units</span>
          </div>
        </div>
      ) : null}

      <h4 className="certificate__subhead">Cited records</h4>
      <ul className="cited-list">
        {certificate.evidence.map((reference) => (
          <EvidenceRow key={reference.source_record_id} reference={reference} />
        ))}
      </ul>
    </div>
  );
}
