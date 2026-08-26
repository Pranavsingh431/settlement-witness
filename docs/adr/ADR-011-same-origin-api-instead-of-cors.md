# ADR-011: The browser reaches the API through its own origin, not through CORS

- Status: Accepted
- Date: 2026-08-26
- Supersedes: none
- Superseded by: none
- Related: [ADR-001](ADR-001-stack-and-modular-monolith.md),
  [ADR-010](ADR-010-import-receipts-are-the-created-resource.md)

## Context

Phase 7 gives the backend a browser interface. The frontend runs on port 5173 in
development and is served by nginx in the container; the backend answers on 8000.
Two different origins, and a browser will not let a page on one call the other
without being told to allow it.

The usual answer is to put the backend's address in the frontend and enable CORS
on the backend. It works in about five minutes and it is the wrong trade here.

## Decision

**The frontend only ever requests same-origin relative paths, and something in
front of it carries `/v1` to the backend.** Vite proxies it in development.
nginx proxies it in the container, to `http://backend:8000` on the compose
network. No host appears anywhere in the client, in an environment variable, or
in the built bundle.

The backend has no CORS policy and gains none.

### Why not CORS

Enabling CORS is a change to what the server accepts, made to suit how the
client is being run. A permissive policy tells every origin that this API is
theirs to call, on an API with no authentication, whose endpoints write facts
and create runs. The convenience is on the development machine; the loosening
ships to whatever the container is deployed onto.

A narrow policy is better and still wrong in a smaller way: it needs to know
which origins exist, which is a deployment fact that has to be configured, kept
correct, and got wrong quietly the first time someone changes a port.

### Why not a configurable API host

Putting the backend's address in the bundle, from an environment variable at
build time, has a different problem: the built artefact stops being portable.
The same image can no longer be run against a different backend without being
rebuilt, and a bundle that names `localhost` works on the machine it was built
on and nowhere else.

A relative path has none of that. The bundle asks for `/v1/imports` and whatever
is serving it decides where that goes.

### The cost

The interface will not work without a proxy in front of it, and a missing proxy
fails in a confusing way: the app shell is returned for `/v1/imports` with
status 200, and every screen reports a malformed response rather than a routing
problem.

That is paid for with tests rather than with a comment. The Vite proxy and the
nginx configuration are both asserted, including that the `/v1` location is
declared before the single page fallback, and the container check requests
`/v1/health` and `/v1/imports` through the frontend's own port and requires an
unknown `/v1` path to return the backend's 404 rather than the shell.

## Consequences

- `make dev` is the supported way to run the interface. Serving the frontend
  without the proxy will not work, deliberately, rather than working against a
  hard-coded host.
- The backend stays as closed as it was. Nothing about adding a browser
  interface changed what the API accepts.
- nginx raises `client_max_body_size` to 64 MB, above its 1 MB default, so that
  the backend's own upload limit is the one that decides. Otherwise a document
  the backend would accept could be refused by the proxy instead, with a
  different error and no receipt.
