# Architecture

## Two cooperating systems

```
                         HTTP API (send one message)
  ┌───────────────┐  ───────────────────────────────▶  ┌─────────┐  SMTP   ┌──────────────────┐
  │  Our Web App  │                                     │ Postal  │ ──────▶ │ Recipient servers │
  │ (FastAPI +    │  ◀───────────────────────────────   │ (MTA)   │         └──────────────────┘
  │  Celery)      │     webhooks (delivered / bounced    └─────────┘
  └───────────────┘             / complaint)
```

- **Postal** is the delivery engine. It owns SMTP delivery, DKIM signing, IP-pool
  assignment, MTA-level suppression, bounce capture, and webhooks. We do **not**
  reimplement any of it, and the web app **never** speaks SMTP to the outside
  world — Postal does.
- **Our web app** is the campaign/list/scheduling layer on top: login, list &
  contact storage, templates, campaign definition & scheduling, the send
  orchestration, list verification, bounce reconciliation, and the dashboard.

The app talks to Postal **only** through Postal's HTTP API (sending) and Postal
**webhooks** (events). That boundary lives behind one interface
(`app/integrations/postal`), so a mock fully substitutes for live Postal in
development and tests.

## Process model

| Process | Role |
|---|---|
| `app` (gunicorn + uvicorn) | FastAPI: admin UI, status API, webhook + unsubscribe endpoints |
| `worker` (Celery) | imports, verification, per-recipient sends, webhook processing |
| `beat` (Celery Beat) | every-minute "dispatch due campaigns" + finalize/stall sweep |
| `postgres` | system of record |
| `redis` | Celery broker + rate-limiter/locks/daily-cap counters |
| `nginx` | TLS termination + reverse proxy |

Async vs sync: FastAPI uses the **async** SQLAlchemy engine (asyncpg); Celery
workers use a **sync** engine (psycopg). Running blocking sessions inside Celery
avoids the pitfalls of driving asyncpg across short-lived per-task event loops —
the right trade-off at ~20 emails/min. The two engines share one set of models.

## Data model (essentials)

- `contacts` — one row per **globally-unique** normalized email; belongs to one
  list. (We use a single `list_id` FK rather than a join table because the hard
  rule "an email may not appear in two lists" makes many-to-many wrong.)
- `suppressions` — global, permanent do-not-send list, keyed by email. **The**
  most important table.
- `campaigns` / `campaign_recipients` / `email_events` — the per-recipient
  **ledger** (snapshotted at send time) + an append-only event log. This is what
  makes sending idempotent, pausable, and reconcilable.
- `templates`, `users`, `app_settings` (editable free-provider blocklist),
  `api_keys` (status API).

All timestamps are stored UTC; timezones are applied only at presentation.

## The sending pipeline (the core)

It is a **queue-driven pipeline, never a loop in a web request.**

```
Beat: dispatch_due_campaigns (every minute)
  └─ find SCHEDULED campaigns with scheduled_at <= now → set SENDING
        └─ materialize_campaign(id)            [snapshot the recipient set]
              └─ send_one(recipient_id)  ×N    [one message each, idempotent]
                    └─ Postal HTTP API
```

### 1. Scheduling & materialization
A `scheduled` campaign whose `scheduled_at` has passed is flipped to `sending`
by Beat. Materialization then creates a `campaign_recipients` row for every
**active** contact in the list, **excluding** (marking `skipped_suppressed`) any
email in the global suppression table — determined with a single LEFT JOIN, not
N lookups. Snapshotting here means later list edits can't corrupt an in-flight
campaign. Materialization is idempotent (re-runs skip already-materialized
contacts), which is also how **resume** works.

### 2. Per-recipient send (`send_one`) — idempotency
Each recipient is one task keyed on its ledger id. Before sending:
1. A Redis `NX` lock (`lock:send:<id>`) ensures only one worker processes a
   recipient at a time.
2. A DB guard returns early if the recipient is no longer `pending`.

Together these guarantee that a retry or a duplicate enqueue **never
double-sends**. On success we persist Postal's message **token** (the id carried
by webhooks) and set `sent`.

### 3. Suppression enforcement (two points)
Suppression is checked at **materialization** and **again inside `send_one`**
right before sending (race safety). A contact suppressed between the two steps is
marked `skipped_suppressed` and never sent.

### 4. Rate pacing
Every send must take a token from **both** a global bucket (default 20/min, kept
under Contabo's ~25/min provider cap) and a **per-recipient-domain** bucket — in
one atomic Redis operation, so a domain-throttled send never burns a global
token. A per-IP-pool **daily cap** enforces the warming schedule. When pacing
blocks a send, the task **re-enqueues a fresh copy with a countdown** rather than
consuming the transient-failure retry budget.

### 5. Failures — and why an API error is not a bounce
- **Transient** Postal/API problems (timeout, 5xx, throttle) → exponential
  backoff retry (up to 5 attempts).
- **Permanent** Postal rejections (bad address, malformed) → recipient `failed`,
  no retry.
- A failed API call is **never** treated as a bounce. **Bounces arrive
  asynchronously via webhook** and are the only thing that suppresses an address.

### 6. Pause / resume / complete
Pausing flips status so new sends stop being picked up; pending recipients are
left untouched. Resuming re-materializes (idempotent) to re-enqueue them. A
campaign auto-completes when no recipients remain `pending`; counters freeze.

## Bounce reconciliation

The webhook endpoint verifies the shared secret on every request, then hands the
payload to a Celery task. Reconciliation:
1. Dedupes on Postal's event uuid (idempotent — Postal may redeliver).
2. Matches the recipient by Postal message token, appends an `email_events` row.
3. On delivery → `delivered`. On **any** failure/complaint → `bounced` **and**
   global suppression.

The suppress decision passes through a single seam, `classify_bounce(event)`,
shipped as "always suppress" (the owner's policy). A future maintainer can swap
in SMTP-status-code logic there without touching the rest of the pipeline.

## Pre-send verification

On upload, contacts are stored `pending_verification` and a background task runs:
- **Layer 1** — syntax/normalize (in-process).
- **Layer 2** — domain/MX (or A-record) check, with **per-domain caching** and
  concurrent async DNS. No MX/A → `invalid` (auto-suppressed); DNS timeout →
  `unknown` (kept sendable); otherwise `valid`.

A `VerificationProvider` interface leaves a clearly-marked slot for an external
API (deeper Layer-4) later — never run from our sending IPs. Layers 3 (role/
disposable) and 4 (SMTP probe) are intentionally **not** built (see DECISIONS).
