/**
 * The small pieces every screen is built from.
 *
 * The badges are the important ones. A decision status is the answer this whole
 * system exists to give, so it is never a bare colour: each badge carries a
 * glyph and the status word, and the four palettes differ in lightness as well
 * as hue so they survive being read in greyscale.
 */

import type { ReactNode } from 'react';

import { describeError } from '../api/errors';
import type { DecisionStatus, ImportOutcome } from '../api/types';

interface Tone {
  readonly modifier: string;
  readonly glyph: string;
  readonly label: string;
}

// Indexed by string, not by the union: the backend owns this vocabulary and
// can add to it, so an unfamiliar status has to fall through to a readable
// default rather than render as nothing.
const DECISION_TONES: Record<string, Tone> = {
  RESOLVED: { modifier: 'resolved', glyph: '✓', label: 'Resolved' },
  EXCEPTION: { modifier: 'exception', glyph: '!', label: 'Exception' },
  INSUFFICIENT_EVIDENCE: { modifier: 'unknown', glyph: '?', label: 'Insufficient evidence' },
  PENDING: { modifier: 'pending', glyph: '·', label: 'Pending' },
};

/** The status of one decision, as a glyph, a colour and a word. */
export function StatusBadge({ status }: { status: DecisionStatus }) {
  const tone = DECISION_TONES[status] ?? {
    modifier: 'neutral',
    glyph: '·',
    label: status,
  };
  return (
    <span className={`badge badge--${tone.modifier}`}>
      <span className="badge__glyph" aria-hidden="true">
        {tone.glyph}
      </span>
      {tone.label}
    </span>
  );
}

const IMPORT_TONES: Record<string, Tone> = {
  ACCEPTED: { modifier: 'resolved', glyph: '✓', label: 'Accepted' },
  DUPLICATE_NO_OP: { modifier: 'neutral', glyph: '=', label: 'Duplicate, no change' },
  REJECTED_INVALID: { modifier: 'danger', glyph: '×', label: 'Rejected, unreadable' },
  REJECTED_CONFLICT: { modifier: 'danger', glyph: '×', label: 'Rejected, conflicting' },
};

/** The outcome of one import attempt. */
export function OutcomeBadge({ outcome }: { outcome: ImportOutcome }) {
  const tone = IMPORT_TONES[outcome] ?? { modifier: 'neutral', glyph: '·', label: outcome };
  return (
    <span className={`badge badge--${tone.modifier}`}>
      <span className="badge__glyph" aria-hidden="true">
        {tone.glyph}
      </span>
      {tone.label}
    </span>
  );
}

export function Panel({
  title,
  note,
  actions,
  children,
}: {
  title: string;
  note?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel" aria-label={title}>
      <div className="panel__head">
        <div>
          <h2>{title}</h2>
          {note ? <p className="panel__note">{note}</p> : null}
        </div>
        {actions}
      </div>
      <div className="panel__body">{children}</div>
    </section>
  );
}

/**
 * A labelled group of statistics.
 *
 * Named so that a reader moving by landmark, and a test asking "what does the
 * run summary say", can both address the numbers as one thing rather than
 * hunting for a word that also appears in the prose around them.
 */
export function Stats({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="stats" role="group" aria-label={label}>
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'resolved' | 'exception' | 'unknown';
}) {
  return (
    <div className={`stat${tone ? ` stat--${tone}` : ''}`}>
      <div className="stat__value">{value}</div>
      <div className="stat__label">{label}</div>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  actions,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      <p>{children}</p>
      {actions ? <div className="empty__actions">{actions}</div> : null}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="loading" role="status">
      Loading {what}…
    </p>
  );
}

/**
 * An error, with the backend's own words and a way to try again.
 *
 * `role="alert"` so a reader using a screen reader is told rather than having
 * to go looking, and a retry button because the commonest failure here is a
 * backend that is not running yet, which is fixed by starting it and retrying.
 */
export function ErrorNotice({
  error,
  onRetry,
  what,
}: {
  error: unknown;
  onRetry?: () => void;
  what: string;
}) {
  return (
    <div className="notice notice--error" role="alert">
      <p className="notice__title">Could not load {what}.</p>
      <p className="notice__body">{describeError(error)}</p>
      {onRetry ? (
        <p style={{ marginTop: 10 }}>
          <button type="button" className="button button--quiet button--small" onClick={onRetry}>
            Try again
          </button>
        </p>
      ) : null}
    </div>
  );
}

export function Facts({ items }: { items: readonly [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {items.map(([key, value]) => (
        <div key={key}>
          <dt className="facts__key">{key}</dt>
          <dd className="facts__value">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
