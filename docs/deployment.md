# Deployment boundary

Settlement Witness is ready to run as a **local demonstration** with Docker
Compose and as a **public, synthetic Vercel preview** for hackathon review. It
is not a multi-tenant public product: it has write endpoints for imports,
bank-finality audits and review events, and it has no application-level identity
or tenancy model.

The public preview is intentionally a shared demonstration workspace. The first
screen loads only four committed synthetic fixtures; it is safe for a reviewer
to inspect, but it is not a place to upload merchant data. Anyone with the link
can add evidence or append a workflow event, so the public preview must never
be presented as an access-controlled customer deployment.

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

## Public Vercel hackathon preview

The committed [`vercel.json`](../vercel.json) uses Vercel Services: the Vite
application is the root service and FastAPI receives only `/v1/*` plus
`/health`. The backend entrypoint is `app.main:app`. It reads the managed
database from `SW_DATABASE_URL` (or the Neon integration's
`SW_DATABASE_DATABASE_URL`); it never writes a SQLite file in the function
filesystem.

Before pushing the public reviewer branch, configure these Vercel settings:

1. Let the Neon integration provide `SW_DATABASE_DATABASE_URL` in the **Preview**
   environment. The backend recognises that integration name directly; do not
   copy its value into a second variable.
   The value is a secret; do not put it in Git, `.env`, a command line, or an
   issue. The application accepts the standard Neon `postgresql://` form and
   uses the pinned Psycopg driver internally.
2. Optionally add `SW_APP_ENV=production` to that same Preview environment.
   Never save platform variables as blank strings: absent optional values use
   their safe defaults, but an explicit value should be meaningful.
3. Disable **Deployment Protection → Vercel Authentication** for this
   hackathon preview so external reviewers can open the submitted URL. Do not
   use a protection bypass or share a bypass secret. This choice is acceptable
   only because the walkthrough is synthetic and the workspace is deliberately
   shared; it would be wrong for merchant data.
4. Push a non-`main` branch. Vercel treats a push to `main` as Production and
   does not apply Preview-only environment variables.

The first cold start migrates the empty PostgreSQL database to the current
schema. PostgreSQL advisory locking serialises concurrent cold starts, and the
same append-only UPDATE/DELETE protections are installed as database triggers.

After Vercel marks the preview ready, use **Load the interactive demo** on the
landing page. It loads four shipped synthetic CSVs server-side, creates a normal
immutable reconciliation run and bank-finality audit, and takes the reviewer to
the evidence trail. It never reads a file from the reviewer's machine.

## Requirements before a public production service

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

For a Production deployment, copy the Neon variables to the **Production**
environment and protect the production domain too. Vercel treats Preview and
Production variables as separate values.

Until identity and tenancy are designed and implemented, access control must be
provided by the deployment environment. A platform choice, domain and access
policy are operator decisions; this repository deliberately does not pretend
they are solved by a Dockerfile.

## Pre-share check

Before giving a reviewer access, run:

```bash
make verify
```

Then demonstrate the evidence path: load the bundled walkthrough, open a
certificate, open the review queue, and explain that bank finality is a separate
conclusion. The exact walkthrough and safe claims are in [submission.md](submission.md).
