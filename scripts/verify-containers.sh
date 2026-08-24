#!/usr/bin/env bash
#
# Build both container images, start them, and check that they actually work.
#
# This is the part of the pipeline that needs Docker, so it is kept out of
# `make ci` and run by `make verify` instead. It checks four things that a
# successful image build on its own does not prove:
#
#   1. the backend answers its health endpoint;
#   2. the frontend serves the bundle on the published host port;
#   3. the frontend still serves unknown paths, so client side routing works;
#   4. neither container runs as root.
#
# The stack is stopped on the way out, including when a check fails.
#
# Stays compatible with bash 3.2, which is the version that ships with macOS.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

readonly BACKEND_URL="http://127.0.0.1:8000/health"
readonly FRONTEND_URL="http://127.0.0.1:5173"
readonly READY_ATTEMPTS=45

info() { printf '\033[0;34m==>\033[0m %s\n' "$1"; }
pass() { printf '\033[0;32mok:\033[0m %s\n' "$1"; }
fail() { printf '\033[0;31mfailed:\033[0m %s\n' "$1" >&2; exit 1; }

shutdown() {
  trap - INT TERM EXIT
  info "Stopping the stack."
  docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}

if ! docker info >/dev/null 2>&1; then
  fail "Docker is not running. Start Docker, then run 'make verify' again. Plain 'make ci' does not need it."
fi

trap shutdown INT TERM EXIT

info "Building both images."
docker compose build

info "Starting the stack."
docker compose up --detach --wait --wait-timeout 180

# --wait already blocks until both health checks report healthy, so reaching
# this point means the images start. The checks below are about behaviour.

check_http() {
  local name="$1" url="$2" attempt status
  for attempt in $(seq 1 "${READY_ATTEMPTS}"); do
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' "${url}" || true)"
    if [ "${status}" = "200" ]; then
      pass "${name} returned 200 from ${url}"
      return 0
    fi
    sleep 1
  done
  docker compose logs --tail 40
  fail "${name} never returned 200 from ${url}. Last status was '${status}'."
}

check_non_root() {
  local service="$1" uid
  uid="$(docker compose exec -T "${service}" id -u 2>/dev/null | tr -d '[:space:]')"
  if [ -z "${uid}" ]; then
    fail "Could not read the UID inside the ${service} container."
  fi
  if [ "${uid}" = "0" ]; then
    fail "The ${service} container runs as root. UID is ${uid}, which must not be 0."
  fi
  pass "${service} container runs as UID ${uid}, which is not root"
}

info "Checking that the services respond."
check_http "backend" "${BACKEND_URL}"
check_http "frontend" "${FRONTEND_URL}/"
check_http "frontend single page fallback" "${FRONTEND_URL}/an/unknown/client/route"

info "Checking the health payload."
health_body="$(curl --silent --fail "${BACKEND_URL}")"
case "${health_body}" in
  *'"status":"ok"'*) pass "backend health payload is ${health_body}" ;;
  *) fail "backend health payload was unexpected: ${health_body}" ;;
esac

info "Checking that neither container runs as root."
check_non_root backend
check_non_root frontend

info "Container verification passed."
