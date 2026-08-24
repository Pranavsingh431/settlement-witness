#!/usr/bin/env bash
#
# Run the backend and frontend dev servers side by side.
#
# Both servers stop together when you press Ctrl-C or when either one exits.
# The script stays compatible with bash 3.2, which is the version that ships
# with macOS, so it avoids `wait -n`.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

info() { printf '\033[0;34m==>\033[0m %s\n' "$1"; }

backend_pid=""
frontend_pid=""

shutdown() {
  trap - INT TERM EXIT
  for pid in "${backend_pid}" "${frontend_pid}"; do
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}

trap shutdown INT TERM EXIT

info "Backend starting on the address in your .env file, by default http://127.0.0.1:8000 with the health check at /health."
(cd backend && uv run python -m app) &
backend_pid="$!"

info "Frontend starting on http://127.0.0.1:5173."
(cd frontend && pnpm run dev) &
frontend_pid="$!"

info "Both servers are running. Press Ctrl-C to stop them."

while kill -0 "${backend_pid}" 2>/dev/null && kill -0 "${frontend_pid}" 2>/dev/null; do
  sleep 1
done

info "One server exited, so the other one is stopping too."
exit 1
