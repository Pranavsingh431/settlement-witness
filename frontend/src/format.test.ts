import { describe, expect, it } from 'vitest';

import { formatMinorUnits, formatTimestamp, humanise, plural, shortHash } from './format';

describe('formatMinorUnits', () => {
  it('groups thousands', () => {
    expect(formatMinorUnits(1000000)).toBe('1,000,000');
  });

  it('leaves a short number alone', () => {
    expect(formatMinorUnits(500)).toBe('500');
  });

  it('handles a group boundary exactly', () => {
    expect(formatMinorUnits(1000)).toBe('1,000');
  });

  it('keeps a negative sign, because an adjustment can be negative', () => {
    expect(formatMinorUnits(-1500)).toBe('-1,500');
  });

  it('renders zero as zero rather than as nothing', () => {
    expect(formatMinorUnits(0)).toBe('0');
  });

  it('adds no currency symbol, because the API sends no currency', () => {
    expect(formatMinorUnits(244100)).not.toMatch(/[$£€₹]/);
  });
});

describe('formatTimestamp', () => {
  it('renders a UTC instant in a fixed shape', () => {
    expect(formatTimestamp('2026-08-24T12:00:00Z')).toBe('2026-08-24 12:00:00 UTC');
  });

  it('converts an offset to UTC rather than showing the offset', () => {
    expect(formatTimestamp('2026-08-24T17:30:00+05:30')).toBe('2026-08-24 12:00:00 UTC');
  });

  it('pads single digit parts', () => {
    expect(formatTimestamp('2026-01-02T03:04:05Z')).toBe('2026-01-02 03:04:05 UTC');
  });

  it('returns an unreadable value unchanged rather than showing Invalid Date', () => {
    expect(formatTimestamp('not a date')).toBe('not a date');
  });
});

describe('shortHash', () => {
  it('shortens a long hash and marks that it was shortened', () => {
    expect(shortHash('a'.repeat(64))).toBe(`${'a'.repeat(12)}…`);
  });

  it('leaves a short value whole', () => {
    expect(shortHash('abc')).toBe('abc');
  });
});

describe('humanise', () => {
  it('turns a code into a sentence fragment', () => {
    expect(humanise('REJECTED_INVALID')).toBe('Rejected invalid');
  });

  it('handles a single word', () => {
    expect(humanise('ACCEPTED')).toBe('Accepted');
  });
});

describe('plural', () => {
  it('uses the singular for one', () => {
    expect(plural(1, 'row', 'rows')).toBe('1 row');
  });

  it('uses the plural for anything else, including zero', () => {
    expect(plural(0, 'row', 'rows')).toBe('0 rows');
    expect(plural(2000, 'row', 'rows')).toBe('2,000 rows');
  });
});
