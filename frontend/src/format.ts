/**
 * Turning API values into text a person reads.
 *
 * Everything here is deterministic. Two people looking at the same run see the
 * same characters, which matters more than matching each reader's locale on a
 * screen whose whole purpose is that two readers can compare what they see.
 */

/**
 * Group an integer with thin separators, without a currency symbol.
 *
 * The API sends minor units and sends no currency with them, so this cannot
 * know whether 244100 is rupees, cents or paise, and inventing a symbol would
 * be inventing a fact. Callers label the number as minor units instead.
 *
 * Written out rather than using `Intl.NumberFormat`, whose grouping depends on
 * the reader's locale and on which ICU data the browser shipped.
 */
export function formatMinorUnits(value: number): string {
  const negative = value < 0;
  const digits = Math.abs(Math.trunc(value)).toString();
  const groups: string[] = [];
  for (let end = digits.length; end > 0; end -= 3) {
    groups.unshift(digits.slice(Math.max(0, end - 3), end));
  }
  return `${negative ? '-' : ''}${groups.join(',')}`;
}

/**
 * Render an ISO timestamp as a fixed UTC string.
 *
 * UTC because the backend stores and reports UTC, and quietly converting to the
 * reader's zone would make two people describing the same audit trail disagree
 * about when something happened.
 */
export function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  const pad = (part: number): string => String(part).padStart(2, '0');
  return (
    `${String(parsed.getUTCFullYear())}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())} ` +
    `${pad(parsed.getUTCHours())}:${pad(parsed.getUTCMinutes())}:${pad(parsed.getUTCSeconds())} UTC`
  );
}

/**
 * Shorten a hash for a table cell.
 *
 * The full value stays available through the element's title and is shown in
 * full on the detail panel, because an auditor comparing a hash needs all of
 * it. This is only to stop a 64 character string from setting a column width.
 */
export function shortHash(hash: string, keep = 12): string {
  return hash.length <= keep ? hash : `${hash.slice(0, keep)}…`;
}

/** Turn `REJECTED_INVALID` into `Rejected invalid`, for prose. */
export function humanise(code: string): string {
  const lower = code.replace(/_/g, ' ').toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/** Return `1 row` or `3 rows`, so a count never reads as `1 rows`. */
export function plural(count: number, one: string, many: string): string {
  return `${formatMinorUnits(count)} ${count === 1 ? one : many}`;
}
