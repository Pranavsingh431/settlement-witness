/**
 * One decision, and why it came out the way it did.
 *
 * The point of this panel is that a passed check, a failed check and a check
 * that could not be evaluated must not look alike. They are three different
 * answers: the rule held, the rule was broken, and nobody knows. A single
 * green tick against everything would be exactly the optimistic summary this
 * project exists to avoid.
 *
 * There is nothing here that changes a decision, because the backend has no
 * endpoint that changes one. A conclusion is immutable and replayable, and a
 * button here suggesting otherwise would be a lie about the system.
 */

import { formatMinorUnits, formatTimestamp, humanise } from '../format';
import type { DecisionView, EvidenceReference, InvariantCheck } from '../api/types';
import { Facts, StatusBadge } from './ui';

interface Mark {
  readonly modifier: string;
  readonly glyph: string;
  readonly label: string;
}

// Indexed by string for the same reason the badges are: an outcome this build
// has not heard of must still render as something a person can read.
const INVARIANT_MARKS: Record<string, Mark> = {
  PASSED: { modifier: 'passed', glyph: '✓', label: 'Held' },
  FAILED: { modifier: 'failed', glyph: '×', label: 'Broken' },
  INSUFFICIENT_INPUT: { modifier: 'missing', glyph: '?', label: 'Could not be checked' },
  NOT_APPLICABLE: { modifier: 'skipped', glyph: '–', label: 'Does not apply' },
};

const EVIDENCE_MARKS: Record<string, Mark> = {
  VERIFIED: { modifier: 'passed', glyph: '✓', label: 'Verified against the stored fact' },
  FACT_NOT_FOUND: { modifier: 'missing', glyph: '?', label: 'No stored fact with this ID' },
  SOURCE_SYSTEM_MISMATCH: { modifier: 'failed', glyph: '×', label: 'Stored under another system' },
  PAYLOAD_HASH_MISMATCH: { modifier: 'failed', glyph: '×', label: 'Stored payload hash differs' },
};

function InvariantRow({ check }: { check: InvariantCheck }) {
  const mark: Mark = INVARIANT_MARKS[check.outcome] ?? {
    modifier: 'skipped',
    glyph: '·',
    label: check.outcome,
  };
  const hasAmounts = check.expected_minor !== null || check.observed_minor !== null;
  return (
    <div className={`check check--${mark.modifier}`}>
      <span className="check__mark" aria-hidden="true">
        {mark.glyph}
      </span>
      <span className="check__title">
        <span className="mono">{check.invariant_id}</span> · {mark.label}
      </span>
      {check.reason_code ? (
        <span className="check__detail">{humanise(check.reason_code)}</span>
      ) : null}
      {hasAmounts ? (
        <div className="amounts">
          <div>
            <span className="amounts__label">Expected</span>
            <span className="amounts__value">
              {check.expected_minor === null ? '—' : formatMinorUnits(check.expected_minor)}
            </span>
          </div>
          <div>
            <span className="amounts__label">Observed</span>
            <span className="amounts__value">
              {check.observed_minor === null ? '—' : formatMinorUnits(check.observed_minor)}
            </span>
          </div>
          <div>
            <span className="amounts__label">Unit</span>
            <span className="amounts__value">minor units</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function EvidenceRow({ reference }: { reference: EvidenceReference }) {
  const mark: Mark =
    reference.verification_outcome === null
      ? { modifier: 'skipped', glyph: '·', label: 'No verification result recorded' }
      : (EVIDENCE_MARKS[reference.verification_outcome] ?? {
          modifier: 'skipped',
          glyph: '·',
          label: reference.verification_outcome,
        });
  return (
    <div className={`check check--${mark.modifier}`}>
      <span className="check__mark" aria-hidden="true">
        {mark.glyph}
      </span>
      <span className="check__title">{mark.label}</span>
      <span className="check__detail">
        <span className="mono">{reference.source_record_id}</span> · {reference.source_system}
      </span>
      <span className="check__detail hash">payload hash {reference.payload_hash}</span>
    </div>
  );
}

export function DecisionCertificate({ decision }: { decision: DecisionView }) {
  const unmet = decision.invariant_results.filter(
    (check) => check.outcome === 'FAILED' || check.outcome === 'INSUFFICIENT_INPUT',
  );
  const brokenRule = decision.invariant_results.some((check) => check.outcome === 'FAILED');

  // Written from what this decision actually records rather than from its
  // status alone. An exception can be raised by a broken invariant or by the
  // baseline reporting a code while every invariant held, and those are
  // different findings. Saying "a rule did not hold" for the second would be
  // describing a failure that did not happen.
  const headline =
    decision.status === 'RESOLVED'
      ? 'Every required invariant held and every citation resolved to a stored fact.'
      : decision.status === 'EXCEPTION'
        ? brokenRule
          ? 'The evidence resolved, and at least one required invariant did not hold.'
          : 'The evidence resolved and every invariant held. This line is still an exception, because the baseline recognised a state it will not resolve on its own.'
        : decision.status === 'INSUFFICIENT_EVIDENCE'
          ? 'This line could not be judged, because the evidence it cites is not all there.'
          : 'This line has not been judged.';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <StatusBadge status={decision.status} />
          <span className="mono">{decision.subject_settlement_line_id}</span>
        </div>
        <p className="page__lede" style={{ marginTop: 8 }}>
          {headline}
        </p>
      </div>

      <Facts
        items={[
          ['Decision ID', <span className="mono">{decision.decision_id}</span>],
          ['Contract version', decision.schema_version],
          ['Decided at', formatTimestamp(decision.created_at)],
          [
            'Citations verified',
            `${formatMinorUnits(decision.verified_evidence_count)} of ${formatMinorUnits(decision.evidence.length)}`,
          ],
        ]}
      />

      {decision.exception_codes.length > 0 ? (
        <div>
          <h3>Exceptions raised</h3>
          <div className="chips" style={{ marginTop: 8 }}>
            {decision.exception_codes.map((code) => (
              <span key={code} className="chip chip--exception">
                {code}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div>
        <h3>Invariant certificate</h3>
        <p className="panel__note" style={{ marginTop: 4 }}>
          {unmet.length === 0
            ? 'Every invariant recorded here held or did not apply.'
            : `${formatMinorUnits(unmet.length)} of ${formatMinorUnits(decision.invariant_results.length)} checks did not hold or could not be run.`}
          {unmet.length === 0 && decision.status === 'EXCEPTION'
            ? ' The exception below is the reason this line is not resolved.'
            : ''}
        </p>
        <div style={{ marginTop: 8 }}>
          {decision.invariant_results.length === 0 ? (
            <p className="panel__note">This decision records no invariant results.</p>
          ) : (
            decision.invariant_results.map((check) => (
              <InvariantRow key={check.invariant_id} check={check} />
            ))
          )}
        </div>
      </div>

      <div>
        <h3>Cited evidence</h3>
        <p className="panel__note" style={{ marginTop: 4 }}>
          Each citation names a stored source record and the hash of its canonical payload. The hash
          is not the document: it is what lets anyone holding the same fact confirm the decision was
          made against it.
        </p>
        <div style={{ marginTop: 8 }}>
          {decision.evidence.length === 0 ? (
            <p className="panel__note">This decision cites no evidence.</p>
          ) : (
            decision.evidence.map((reference) => (
              <EvidenceRow key={reference.source_record_id} reference={reference} />
            ))
          )}
        </div>
      </div>

      {decision.reason_codes.length > 0 ? (
        <div>
          <h3>Reason codes</h3>
          <div className="chips" style={{ marginTop: 8 }}>
            {decision.reason_codes.map((code) => (
              <span key={code} className="chip">
                {code}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {decision.linked_event_ids.length > 0 ? (
        <div>
          <h3>Linked provider events</h3>
          <div className="chips" style={{ marginTop: 8 }}>
            {decision.linked_event_ids.map((id) => (
              <span key={id} className="chip mono">
                {id}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <p className="caveat">
        Decisions are immutable. This interface can show why a line was decided as it was, and it
        cannot change that decision: the backend has no endpoint that edits one, deliberately, so
        that a stored conclusion stays replayable against the evidence it cites.
      </p>
    </div>
  );
}
