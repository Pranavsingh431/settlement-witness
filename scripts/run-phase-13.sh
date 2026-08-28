#!/usr/bin/env bash
# Execute the fixed, pre-registered Phase 13 hosted shadow-evaluation protocol.
#
# The only process that can contact a hosted provider below is
# `python -m app.ai.live_shadow --allow-network`.  The Phase 13 helper runs
# before and after it only to freeze non-secret provenance, hash local state,
# and produce a secret-free summary.

set -u -o pipefail

phase13_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase13_env="$phase13_root/.env.ai"

if [ ! -f "$phase13_env" ]; then
  echo "error: .env.ai is not present; no hosted evaluation was started and no score exists." >&2
  exit 2
fi

# This file is local, ignored, and supplied by the operator. `set -x` is never
# enabled, so loading it cannot echo a credential. The Python preflight below
# validates all six required settings without printing any value.
set -a
# shellcheck disable=SC1090
. "$phase13_env"
set +a

phase13_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
phase13_output="$phase13_root/results/phase-13/$phase13_stamp"
if ! mkdir -p "$phase13_root/results/phase-13" || ! mkdir "$phase13_output"; then
  echo "error: could not create a new ignored Phase 13 results directory." >&2
  exit 2
fi

cd "$phase13_root/backend" || exit 2

# This is the pre-registration point. It writes the fixed count (three),
# commit, corpus, harness and non-secret host settings before a provider is
# constructed. A configuration refusal exits here, before a hosted request.
uv run python -m app.ai.phase13 preflight --output "$phase13_output/plan.json" || exit $?

phase13_exit=0
for phase13_run in 1 2 3; do
  uv run python -m app.ai.phase13 database-proof \
    --output "$phase13_output/database-before-$phase13_run.json" || exit $?

  # No retry: one invocation is one of the three declared attempts. Typed host
  # failures are returned as a completed local receipt by live_shadow and are
  # therefore recorded and the protocol proceeds to the next declared run.
  uv run python -m app.ai.live_shadow --allow-network \
    --output "$phase13_output/raw-receipt-$phase13_run.json"
  phase13_exit=$?

  uv run python -m app.ai.phase13 database-proof \
    --output "$phase13_output/database-after-$phase13_run.json" || exit $?

  if [ "$phase13_exit" -ne 0 ]; then
    uv run python -m app.ai.phase13 record-local-failure \
      --plan "$phase13_output/plan.json" \
      --run-ordinal "$phase13_run" \
      --before "$phase13_output/database-before-$phase13_run.json" \
      --after "$phase13_output/database-after-$phase13_run.json" \
      --output "$phase13_output/run-$phase13_run.json" || exit $?
    echo "error: Phase 13 stopped after an incomplete declared run; no retry was made." >&2
    exit "$phase13_exit"
  fi

  uv run python -m app.ai.phase13 record \
    --plan "$phase13_output/plan.json" \
    --run-ordinal "$phase13_run" \
    --before "$phase13_output/database-before-$phase13_run.json" \
    --after "$phase13_output/database-after-$phase13_run.json" \
    --receipt "$phase13_output/raw-receipt-$phase13_run.json" \
    --output "$phase13_output/run-$phase13_run.json" || exit $?
done

uv run python -m app.ai.phase13 summary \
  --plan "$phase13_output/plan.json" \
  --run "$phase13_output/run-1.json" \
  --run "$phase13_output/run-2.json" \
  --run "$phase13_output/run-3.json" \
  --output "$phase13_output/summary.json" || exit $?

echo "Phase 13 local artifacts: $phase13_output"
