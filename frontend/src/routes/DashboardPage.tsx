/** The public Track 04 demonstration: one batch, its measures and its exceptions. */

import { useState } from 'react';
import { Stat, Stats } from '../components/ui';
import { runDemoBatch } from '../api/client';
import { describeError } from '../api/errors';
import type { DemoBatchResult, MeasuredRate } from '../api/types';
import { formatMinorUnits, humanise } from '../format';

function percentage(rate: MeasuredRate): string {
  return rate.value === null ? '—' : `${(rate.value * 100).toFixed(1)}%`;
}

function BatchHero({ loading, onRun }: { loading: boolean; onRun: () => void }) {
  return (
    <section className="track-hero" aria-labelledby="track-title">
      <div className="track-hero__copy">
        <p className="eyebrow eyebrow--on-blue">Track 04 · AI Finance Controller</p>
        <h1 id="track-title">Close the batch. Surface what doesn&apos;t reconcile.</h1>
        <p>
          A 59-case synthetic payment batch runs through the same strict importer, evidence verifier
          and reconciliation baseline as the product—not a hand-picked match.
        </p>
        <div className="track-hero__actions">
          <button className="button button--light" type="button" disabled={loading} onClick={onRun}>
            {loading ? 'Running 59-case batch…' : 'Run the 59-case batch'}
          </button>
        </div>
        <p className="track-hero__note">
          Read-only synthetic demo. No browser file, merchant record or shared audit history is
          created. Use the audit workspace with a local, private evidence set.
        </p>
      </div>

      <div className="track-flow" aria-label="Batch reconciliation flow">
        <p className="track-flow__label">One finance-ops loop</p>
        <ol>
          <li>
            <span>01</span>
            <div>
              <strong>Payment events</strong>
              <small>captures, refunds, returns</small>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <strong>Settlement lines</strong>
              <small>gross, fee, tax and net checks</small>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <strong>Payout records</strong>
              <small>batch totals and payout linkage</small>
            </div>
          </li>
          <li>
            <span>04</span>
            <div>
              <strong>Decision ledger</strong>
              <small>matches, exceptions and missing evidence</small>
            </div>
          </li>
        </ol>
      </div>
    </section>
  );
}

function TrackCriteria() {
  return (
    <section className="track-criteria" aria-label="Track 04 fit">
      <div>
        <span className="track-criteria__number">50+</span>
        <p>
          <strong>Batch-scale input</strong>
          <span>59 scenarios across payment, settlement and payout sources.</span>
        </p>
      </div>
      <div>
        <span className="track-criteria__number">2</span>
        <p>
          <strong>Measures, not vibes</strong>
          <span>Auto-match rate plus strict manifest agreement are kept distinct.</span>
        </p>
      </div>
      <div>
        <span className="track-criteria__number">AI</span>
        <p>
          <strong>AI proposes; verifier decides</strong>
          <span>Candidate links cannot resolve, alter or hide an evidence-backed decision.</span>
        </p>
      </div>
    </section>
  );
}

function BatchResult({ batch }: { batch: DemoBatchResult }) {
  const unresolved = batch.exception_count + batch.insufficient_evidence_count;

  return (
    <section className="batch-result" aria-label="59-case batch result">
      <header className="batch-result__head">
        <div>
          <p className="eyebrow">Batch result</p>
          <h2>The controller finished every line—and kept the hard ones open.</h2>
          <p>
            {formatMinorUnits(batch.decision_count)} settlement decisions from{' '}
            {formatMinorUnits(batch.source_record_count)} synthetic source records in{' '}
            {formatMinorUnits(batch.processing_duration_ms)} ms.
          </p>
        </div>
        <div className="batch-result__speed" aria-label="Measured throughput">
          <strong>{formatMinorUnits(batch.throughput_lines_per_second)}</strong>
          <span>lines / second</span>
        </div>
      </header>

      <Stats label="Batch outcome">
        <Stat label="Auto-matched" value={formatMinorUnits(batch.resolved_count)} tone="resolved" />
        <Stat
          label="Exceptions surfaced"
          value={formatMinorUnits(batch.exception_count)}
          tone="exception"
        />
        <Stat
          label="Need more evidence"
          value={formatMinorUnits(batch.insufficient_evidence_count)}
          tone="unknown"
        />
        <Stat label="Source records checked" value={formatMinorUnits(batch.source_record_count)} />
      </Stats>

      <div className="batch-result__detail">
        <div className="match-rate">
          <p className="match-rate__label">Auto-match rate</p>
          <strong>{percentage(batch.auto_match_rate)}</strong>
          <p>
            {formatMinorUnits(batch.auto_match_rate.numerator)} of{' '}
            {formatMinorUnits(batch.auto_match_rate.denominator)} lines were supported without
            manual follow-up.
          </p>
          <span className="match-rate__rule">
            Operational outcome, not a production-accuracy claim.
          </span>
        </div>

        <div className="exception-ledger">
          <div className="exception-ledger__head">
            <div>
              <p className="eyebrow">Honest exception list</p>
              <h3>{formatMinorUnits(unresolved)} lines did not auto-resolve.</h3>
            </div>
            <span>
              {formatMinorUnits(batch.exception_recall.numerator)} expected findings caught
            </span>
          </div>
          <ul>
            {batch.exception_breakdown.map((exception) => (
              <li key={exception.code}>
                <span>{humanise(exception.code)}</span>
                <strong>{formatMinorUnits(exception.finding_count)}</strong>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <footer className="batch-result__footer">
        <p>
          <strong>Contract agreement:</strong>{' '}
          {formatMinorUnits(batch.contract_agreement.numerator)} /{' '}
          {formatMinorUnits(batch.contract_agreement.denominator)} scenarios matched the independent
          manifest on status, exact exception set and cited evidence. False resolutions:{' '}
          {formatMinorUnits(batch.false_resolution_rate.numerator)} /{' '}
          {formatMinorUnits(batch.false_resolution_rate.denominator)}.
        </p>
        <p>{batch.limitation}</p>
      </footer>
    </section>
  );
}

export function DashboardPage() {
  const [batch, setBatch] = useState<DemoBatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function runBatch(): Promise<void> {
    if (loading) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setBatch(await runDemoBatch());
    } catch (cause) {
      setError(cause);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <BatchHero
        loading={loading}
        onRun={() => {
          void runBatch();
        }}
      />
      <TrackCriteria />
      {error ? (
        <section className="demo-error" role="alert">
          <strong>The batch could not run.</strong> {describeError(error)}
          <button
            type="button"
            className="button button--quiet button--small"
            onClick={() => {
              void runBatch();
            }}
          >
            Try again
          </button>
        </section>
      ) : null}
      {batch ? <BatchResult batch={batch} /> : null}
    </>
  );
}
