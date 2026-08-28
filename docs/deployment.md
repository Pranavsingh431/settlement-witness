# Deployment boundary

Settlement Witness is ready to run as a **local demonstration** with Docker
Compose. It is not ready to be placed directly on the public internet: it has
write endpoints for imports, bank-finality audits and review events, and it
intentionally has no authentication or multi-tenancy.

This is a safety boundary, not a caveat to work around. A public URL without an
access-control layer would let an unauthorised visitor write to the demonstration
audit trail.

## Local demonstration

```bash
make docker-up
```

Open <http://127.0.0.1:5173>. The backend is available only on
`127.0.0.1:8000`; the frontend reaches it through the same-origin `/v1` proxy.
Both images run as unprivileged users. The named
`settlement_witness_data` volume preserves data across normal container
restarts. To start fresh after a demo:

```bash
make docker-down
```

That command deliberately removes the local volume and its SQLite database.

## Requirements before a remote demonstration

Use a deployment platform or reverse proxy that provides all of these controls:

1. TLS terminates before a browser can reach the application.
2. An access-control layer protects **every** frontend and API route. Do not
   publish the backend port separately; it must remain private to the frontend
   proxy.
3. `/srv/data` is mounted on persistent storage and backed up. It holds the
   append-only source facts, receipts, runs, review events and bank audits.
4. The frontend is the only public process. It proxies `/v1` to the private
   backend over the platform's internal network.
5. The local `.env.ai` file is not copied into an image, container, secret
   store or deployment configuration. Hosted shadow evaluation is a local,
   corpus-only command and does not belong in the running application.

Until identity and tenancy are designed and implemented, access control must be
provided by the deployment environment. A platform choice, domain and access
policy are operator decisions; this repository deliberately does not pretend
they are solved by a Dockerfile.

## Pre-share check

Before giving a reviewer access, run:

```bash
make verify
```

Then demonstrate the evidence path from the committed fixtures: import the
three CSVs, create a reconciliation run, open a certificate, open the review
queue, and explain that bank finality is a separate conclusion. The exact
walkthrough and safe claims are in [submission.md](submission.md).
