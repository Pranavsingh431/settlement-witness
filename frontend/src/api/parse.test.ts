/**
 * Tests for the response validators.
 *
 * They exist so that a field the interface reads cannot arrive missing and be
 * rendered as an empty cell, which would look like a fact about the data rather
 * than a defect in the plumbing. So the cases worth testing are the ones where
 * a field is absent or the wrong type.
 */

import { describe, expect, it } from 'vitest';

import { MalformedResponseError } from './errors';
import { parseDecision, parseReceipt, parseRunSummary } from './parse';
import { ACCEPTED_RECEIPT, RESOLVED_DECISION, RUN } from '../test/fixtures';

/** Return a copy of a payload with one field removed, as a truncated response would arrive. */
function without<T extends object>(source: T | undefined, key: keyof T): Record<string, unknown> {
  return Object.fromEntries(Object.entries({ ...source }).filter(([name]) => name !== String(key)));
}

describe('parseRunSummary', () => {
  it('accepts a real run', () => {
    expect(parseRunSummary(RUN)).toEqual(RUN);
  });

  it('refuses something that is not an object', () => {
    expect(() => parseRunSummary('a run')).toThrow(MalformedResponseError);
    expect(() => parseRunSummary(null)).toThrow(/not an object/);
    expect(() => parseRunSummary([RUN])).toThrow(/not an object/);
  });

  it('refuses a missing text field and names it', () => {
    expect(() => parseRunSummary(without(RUN, 'run_id'))).toThrow(/run_id/);
  });

  it('refuses a number field that arrived as text', () => {
    expect(() => parseRunSummary({ ...RUN, fact_count: '10' })).toThrow(/fact_count/);
  });

  it('refuses a number field that is not finite', () => {
    expect(() => parseRunSummary({ ...RUN, fact_count: Number.NaN })).toThrow(/fact_count/);
  });

  it('refuses counts that are not an object', () => {
    expect(() => parseRunSummary({ ...RUN, status_counts: 3 })).toThrow(/status_counts/);
  });
});

describe('parseDecision', () => {
  it('accepts a real decision', () => {
    expect(parseDecision(RESOLVED_DECISION)).toEqual(RESOLVED_DECISION);
  });

  it('refuses a missing list', () => {
    expect(() => parseDecision(without(RESOLVED_DECISION, 'evidence'))).toThrow(/evidence/);
  });

  it('refuses a list of identifiers holding something that is not text', () => {
    expect(() => parseDecision({ ...RESOLVED_DECISION, linked_event_ids: ['ok', 7] })).toThrow(
      /linked_event_ids/,
    );
  });

  it('keeps a null verification outcome, which is a real value', () => {
    const decision = parseDecision({
      ...RESOLVED_DECISION,
      evidence: [{ ...RESOLVED_DECISION.evidence[0], verification_outcome: null }],
    });

    expect(decision.evidence[0]?.verification_outcome).toBeNull();
  });

  it('treats an absent nullable field as null rather than refusing it', () => {
    const first = RESOLVED_DECISION.invariant_results[0];
    const decision = parseDecision({
      ...RESOLVED_DECISION,
      invariant_results: [without(first, 'reason_code')],
    });

    expect(decision.invariant_results[0]?.reason_code).toBeNull();
  });

  it('refuses a nullable text field holding a number', () => {
    expect(() =>
      parseDecision({
        ...RESOLVED_DECISION,
        evidence: [{ ...RESOLVED_DECISION.evidence[0], verification_outcome: 7 }],
      }),
    ).toThrow(/verification_outcome/);
  });

  it('refuses a nullable number field holding text', () => {
    expect(() =>
      parseDecision({
        ...RESOLVED_DECISION,
        invariant_results: [{ ...RESOLVED_DECISION.invariant_results[0], expected_minor: '1' }],
      }),
    ).toThrow(/expected_minor/);
  });

  it('keeps a zero amount, which is not the same as an absent one', () => {
    const decision = parseDecision({
      ...RESOLVED_DECISION,
      invariant_results: [{ ...RESOLVED_DECISION.invariant_results[0], expected_minor: 0 }],
    });

    expect(decision.invariant_results[0]?.expected_minor).toBe(0);
  });

  it('accepts a status this build has not heard of, rather than losing the run', () => {
    // The backend owns these vocabularies and can add to them. Refusing a whole
    // run over one unfamiliar code would be worse than showing the code.
    const decision = parseDecision({ ...RESOLVED_DECISION, status: 'SOMETHING_NEW' });

    expect(decision.status).toBe('SOMETHING_NEW');
  });
});

describe('parseReceipt', () => {
  it('accepts a real receipt', () => {
    expect(parseReceipt(ACCEPTED_RECEIPT)).toEqual(ACCEPTED_RECEIPT);
  });

  it('refuses a missing true or false field and names it', () => {
    expect(() => parseReceipt(without(ACCEPTED_RECEIPT, 'wrote_facts'))).toThrow(/wrote_facts/);
  });

  it('refuses a true or false field that arrived as text', () => {
    expect(() => parseReceipt({ ...ACCEPTED_RECEIPT, wrote_facts: 'true' })).toThrow(/wrote_facts/);
  });

  it('keeps a null failure detail, which an accepted import has', () => {
    expect(parseReceipt(ACCEPTED_RECEIPT).failure_detail).toBeNull();
  });
});
