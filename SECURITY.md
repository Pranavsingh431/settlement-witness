# Security

## Reporting a vulnerability

Report suspected vulnerabilities through GitHub private vulnerability reporting on this
repository, under the Security tab. Please do not open a public issue for a vulnerability.

Include the affected version or commit, what an attacker could do, and the steps to reproduce it.
You will get an acknowledgement within seven days.

This is a personal project with no service level agreement. There is no bug bounty.

## Scope

This repository holds a reconciliation tool that runs on your own machine or your own
infrastructure. It is not a hosted service, so there is no production environment to attack. The
useful reports are about the code, the container images, the dependency set and the developer
workflow.

## What the project does to reduce risk

- Both lockfiles are committed, and every install uses the frozen lockfile. Builds resolve to the
  exact versions that were reviewed.
- Continuous integration audits both lockfiles for known vulnerabilities on every push and every
  pull request.
- Continuous integration scans the full git history for leaked secrets with gitleaks on every push
  and every pull request.
- Dependabot opens weekly update pull requests for the backend, the frontend, the GitHub Actions
  and the container base images.
- pnpm 10 does not run dependency lifecycle scripts unless a package is listed explicitly, which
  limits what a compromised package can do at install time.
- Both container images run as an unprivileged user. The frontend runtime image contains only the
  built static files and nginx, with no build tooling.
- The backend validates its settings at startup. An invalid value stops the process instead of
  causing undefined behaviour later.

## Secrets

- `.env` is ignored by git. Only `.env.example` is committed, and it holds no real values.
- Never put a secret in `frontend/`. Anything the frontend bundle can read is public.
- Never log a secret, a raw request signature or a full account identifier.

If you believe a secret has been committed, treat it as compromised. Rotate it first, then remove
it from the history.

## Conventions that later phases must keep

- All monetary amounts are integers in minor units.
- Ingestion is idempotent and tolerates duplicate and out of order events.
- Any signature is verified on the server. A signing secret never reaches the browser.
- The controller never edits financial records. It only produces reviewable work items.
- Calls to an external provider default to read only and to test credentials.
