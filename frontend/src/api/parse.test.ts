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
import {
  parseBankFinalityAuditDetail,
  parseBankFinalityAuditPage,
  parseBankFinalityCertificate,
  parseDemoBootstrap,
  parseDecision,
  parseReceipt,
  parseReviewEventReceipt,
  parseReviewQueueItem,
  parseReviewQueuePage,
  parseRunSummary,
} from './parse';
import {
  ACCEPTED_RECEIPT,
  BANK_AUDIT,
  BANK_AUDITS,
  BANK_AUDIT_DETAIL,
  BASELINE_NOTE,
  CLOSED_ITEM,
  DEMO_BOOTSTRAP,
  EMPTY_REVIEW_QUEUE,
  OPEN_ITEM,
  RESOLVED_DECISION,
  REVIEW_QUEUE,
  NO_BANK_AUDITS,
  RUN,
  UNKNOWN_ITEM,
  UNLINKABLE_CERTIFICATE,
  VERIFIED_CERTIFICATE,
} from '../test/fixtures';

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

describe('parseDemoBootstrap', () => {
  it('accepts the walkthrough result and its existing conclusion shapes', () => {
    expect(parseDemoBootstrap(DEMO_BOOTSTRAP)).toEqual(DEMO_BOOTSTRAP);
  });

  it('refuses a missing loaded-now flag rather than treating it as false', () => {
    expect(() =>
      parseDemoBootstrap({
        ...DEMO_BOOTSTRAP,
        fixture_results: [without(DEMO_BOOTSTRAP.fixture_results[0], 'loaded_now')],
      }),
    ).toThrow(/loaded_now/);
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

describe('parseReviewQueuePage', () => {
  it('accepts a real queue', () => {
    expect(parseReviewQueuePage(REVIEW_QUEUE)).toEqual(REVIEW_QUEUE);
  });

  it('accepts an empty queue', () => {
    expect(parseReviewQueuePage(EMPTY_REVIEW_QUEUE)).toEqual(EMPTY_REVIEW_QUEUE);
  });

  it('refuses something that is not an object', () => {
    expect(() => parseReviewQueuePage('a queue')).toThrow(/not an object/);
  });

  it('refuses a page missing its counts', () => {
    expect(() => parseReviewQueuePage(without(REVIEW_QUEUE, 'total'))).toThrow(/total/);
    expect(() => parseReviewQueuePage(without(REVIEW_QUEUE, 'open_total'))).toThrow(/open_total/);
  });

  it('refuses a page missing the note saying the baseline is unchanged', () => {
    expect(() => parseReviewQueuePage(without(REVIEW_QUEUE, 'baseline_unchanged_note'))).toThrow(
      /baseline_unchanged_note/,
    );
  });

  it('refuses a page whose items are not a list', () => {
    expect(() => parseReviewQueuePage({ ...REVIEW_QUEUE, items: 'none' })).toThrow(/items/);
  });
});

describe('parseReviewQueueItem', () => {
  it('accepts a real item with its timeline', () => {
    expect(parseReviewQueueItem(UNKNOWN_ITEM)).toEqual(UNKNOWN_ITEM);
  });

  it('refuses an item missing its baseline status', () => {
    expect(() => parseReviewQueueItem(without(OPEN_ITEM, 'baseline_status'))).toThrow(
      /baseline_status/,
    );
  });

  it('refuses an item missing its fingerprint', () => {
    expect(() => parseReviewQueueItem(without(OPEN_ITEM, 'decision_fingerprint'))).toThrow(
      /decision_fingerprint/,
    );
  });

  it('refuses an item missing its workflow state', () => {
    expect(() => parseReviewQueueItem(without(OPEN_ITEM, 'workflow_state'))).toThrow(
      /workflow_state/,
    );
  });

  it('refuses an event missing its sequence, which is what orders the timeline', () => {
    const broken = {
      ...UNKNOWN_ITEM,
      events: [without(UNKNOWN_ITEM.events[0], 'sequence')],
    };

    expect(() => parseReviewQueueItem(broken)).toThrow(/sequence/);
  });

  it('accepts an event with no note', () => {
    const item = parseReviewQueueItem(CLOSED_ITEM);

    expect(item.events[0]?.note).toBeNull();
  });

  it('refuses a note that is not text', () => {
    const broken = {
      ...UNKNOWN_ITEM,
      events: [{ ...UNKNOWN_ITEM.events[0], note: 7 }],
    };

    expect(() => parseReviewQueueItem(broken)).toThrow(/note/);
  });
});

describe('parseReviewEventReceipt', () => {
  it('accepts a real receipt', () => {
    const receipt = {
      event: UNKNOWN_ITEM.events[0],
      workflow_state: 'WAITING_FOR_EVIDENCE',
      baseline_status: 'INSUFFICIENT_EVIDENCE',
      baseline_unchanged_note: BASELINE_NOTE,
    };

    expect(parseReviewEventReceipt(receipt)).toEqual(receipt);
  });

  it('refuses a receipt with no baseline status', () => {
    const broken = {
      event: UNKNOWN_ITEM.events[0],
      workflow_state: 'ESCALATED',
      baseline_unchanged_note: BASELINE_NOTE,
    };

    expect(() => parseReviewEventReceipt(broken)).toThrow(/baseline_status/);
  });
});

describe('parseBankFinalityAuditPage', () => {
  it('accepts a real page', () => {
    expect(parseBankFinalityAuditPage(BANK_AUDITS)).toEqual(BANK_AUDITS);
  });

  it('accepts a page with no audits', () => {
    expect(parseBankFinalityAuditPage(NO_BANK_AUDITS)).toEqual(NO_BANK_AUDITS);
  });

  it('refuses a page missing the note saying the two are separate', () => {
    expect(() =>
      parseBankFinalityAuditPage(without(BANK_AUDITS, 'settlement_and_finality_are_separate')),
    ).toThrow(/settlement_and_finality_are_separate/);
  });

  it('refuses an audit missing its verified count', () => {
    const broken = { ...BANK_AUDITS, audits: [without(BANK_AUDIT, 'verified_payout_count')] };

    expect(() => parseBankFinalityAuditPage(broken)).toThrow(/verified_payout_count/);
  });

  it('refuses an audit whose outcome counts are not numbers', () => {
    const broken = {
      ...BANK_AUDITS,
      audits: [{ ...BANK_AUDIT, outcome_counts: { VERIFIED_BANK_CREDIT: 'one' } }],
    };

    expect(() => parseBankFinalityAuditPage(broken)).toThrow(/non-numeric count/);
  });
});

describe('parseBankFinalityAuditDetail', () => {
  it('accepts a real audit with its certificates', () => {
    expect(parseBankFinalityAuditDetail(BANK_AUDIT_DETAIL)).toEqual(BANK_AUDIT_DETAIL);
  });

  it('refuses an audit whose certificates are not a list', () => {
    expect(() =>
      parseBankFinalityAuditDetail({ ...BANK_AUDIT_DETAIL, certificates: 'none' }),
    ).toThrow(/certificates/);
  });
});

describe('parseBankFinalityCertificate', () => {
  it('accepts a verified certificate', () => {
    expect(parseBankFinalityCertificate(VERIFIED_CERTIFICATE)).toEqual(VERIFIED_CERTIFICATE);
  });

  it('accepts a certificate that compared nothing', () => {
    const parsed = parseBankFinalityCertificate(UNLINKABLE_CERTIFICATE);

    expect(parsed.bank_reference).toBeNull();
    expect(parsed.observed_amount_minor).toBeNull();
    expect(parsed.observed_direction).toBeNull();
  });

  it('refuses a certificate missing its outcome', () => {
    expect(() => parseBankFinalityCertificate(without(VERIFIED_CERTIFICATE, 'outcome'))).toThrow(
      /outcome/,
    );
  });

  it('refuses a certificate whose matched ids are not text', () => {
    const broken = { ...VERIFIED_CERTIFICATE, matched_bank_transaction_ids: [7] };

    expect(() => parseBankFinalityCertificate(broken)).toThrow(/non-text entry/);
  });

  it('refuses a certificate whose observed amount is not a number', () => {
    const broken = { ...VERIFIED_CERTIFICATE, observed_amount_minor: '1220500' };

    expect(() => parseBankFinalityCertificate(broken)).toThrow(/observed_amount_minor/);
  });
});
