#!/usr/bin/env bash
#
# Prepare a fresh clone for development.
#
# The script checks the required toolchains, installs the locked backend and
# frontend dependencies, and creates a local .env file. It is safe to run again
# at any time.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

# The supported range matches the "engines.node" field in frontend/package.json
# and the line named in frontend/.nvmrc. The project standardises on the Node 24
# LTS line and supports nothing else, so that the version a contributor runs, the
# version the container builds on and the version CI uses are always the same.
# Newer lines such as Node 26 are rejected on purpose, not by oversight.
readonly SUPPORTED_NODE=">=24 <25"

info() { printf '\033[0;34m==>\033[0m %s\n' "$1"; }
fail() { printf '\033[0;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

suggest_supported_node() {
  local candidate
  for candidate in /opt/homebrew/opt/node@24/bin /usr/local/opt/node@24/bin; do
    if [ -x "${candidate}/node" ]; then
      printf '  A supported Node is already installed here:\n    export PATH="%s:$PATH"\n' "${candidate}" >&2
      return
    fi
  done
  printf '  Install the Node 24 LTS line, for example with "brew install node@24", or use a version manager such as nvm or fnm with frontend/.nvmrc.\n' >&2
}

require_node() {
  if ! command -v node >/dev/null 2>&1; then
    printf '\033[0;31merror:\033[0m Node.js is not installed. This repository needs Node %s.\n' "${SUPPORTED_NODE}" >&2
    suggest_supported_node
    exit 1
  fi

  if ! node -e 'const major = Number(process.versions.node.split(".")[0]); process.exit(major === 24 ? 0 : 1);'; then
    printf '\033[0;31merror:\033[0m Node %s is not supported. This repository needs Node %s.\n' "$(node --version)" "${SUPPORTED_NODE}" >&2
    suggest_supported_node
    exit 1
  fi

  info "Using Node $(node --version)."
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    info "Using $(uv --version)."
    return
  fi

  if command -v brew >/dev/null 2>&1; then
    info "Installing uv with Homebrew."
    brew install uv
    return
  fi

  if command -v pipx >/dev/null 2>&1; then
    info "Installing uv with pipx."
    pipx install uv
    return
  fi

  fail "uv is not installed. Install it with 'brew install uv' or 'pipx install uv', or follow the instructions at https://docs.astral.sh/uv/getting-started/installation/, then run 'make setup' again."
}

ensure_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    info "Using pnpm $(pnpm --version)."
    return
  fi

  if command -v corepack >/dev/null 2>&1; then
    info "Enabling pnpm with corepack."
    if corepack enable pnpm; then
      return
    fi
    fail "corepack could not enable pnpm. Install pnpm another way, for example 'npm install --global pnpm', then run 'make setup' again."
  fi

  fail "pnpm is not installed. Install it with 'npm install --global pnpm' or enable corepack, then run 'make setup' again."
}

require_node
ensure_uv
ensure_pnpm

info "Installing backend dependencies from backend/uv.lock."
(cd backend && uv sync --all-groups --frozen)

info "Installing frontend dependencies from frontend/pnpm-lock.yaml."
(cd frontend && pnpm install --frozen-lockfile)

if [ ! -f .env ]; then
  cp .env.example .env
  info "Created .env from .env.example."
else
  info "Kept the existing .env file."
fi

info "Setup finished. Run 'make ci' to check the repository, or 'make dev' to start the servers."
