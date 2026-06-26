# In-House Email Marketing

A self-hosted email marketing application that replaces a third-party ESP and
sends from our own IPs via a self-hosted [Postal](https://github.com/postalserver/postal)
mail server. It manages contact lists, templates, and scheduled campaigns;
verifies lists before sending; paces delivery for B2B deliverability; and
reconciles bounces into a global suppression list.

Target volume: ~300,000 emails/month (~10,000/day), B2B.

Design priority order: **(1) correct & reliable sending, (2) maintainability,
(3) low operating cost, (4) features.**

## Architecture at a glance

```
[Our Web App] --HTTP API--> [Postal] --SMTP--> [Recipient mail servers]
      ^                          |
      |--------- webhooks -------|   (delivered / bounced / complaint events)
```

Our app owns lists, templates, campaigns, scheduling, send orchestration, list
verification, bounce reconciliation, and the dashboard. Postal owns SMTP
delivery, DKIM, IP pools, and bounce capture. See [ARCHITECTURE.md](ARCHITECTURE.md).

## Tech stack

- **Backend:** Python 3.12 + FastAPI (async), Pydantic, server-rendered Jinja2 UI
- **Database:** PostgreSQL via SQLAlchemy 2 (async) + Alembic
- **Queue/worker:** Celery + Redis (token-bucket rate limiting, Beat scheduler)
- **Mail engine:** Postal (HTTP API for sending, webhooks for events) — with a
  mock so the whole app runs and tests without live Postal
- **Proxy/TLS:** Nginx + Let's Encrypt (certbot)
- **Packaging:** Docker Compose (app, worker, beat, postgres, redis, nginx)

## Repository layout

```
app/
  api/            JSON status API, Postal webhook, public unsubscribe
  web/            server-rendered admin UI routes
  services/       business logic (suppression, sending helpers, verification,
                  csv import, lists/templates/campaigns, stats, preflight)
  tasks/          Celery tasks (sending, scheduler/beat, verification, imports, webhooks)
  integrations/postal/   Postal client (real + mock) and webhook verification
  models/         SQLAlchemy models (portable: JSONB on PG, JSON on SQLite)
  templates/      Jinja2 HTML  •  static/  CSS
migrations/       Alembic
scripts/seed_admin.py
deploy/nginx/ , deploy/init-letsencrypt.sh
tests/            pytest critical-path suite
```

## Quick start (Docker, on the server)

```bash
git clone https://github.com/reidevaliaj/emailmarketing.git
cd emailmarketing
cp .env.example .env        # then edit: secrets, POSTGRES_URL (postgres host), Postal creds
docker compose up -d --build
# migrations + admin seed run automatically (the `migrate` service)
```

Then obtain TLS certs and serve HTTPS:

```bash
EMAIL=you@cod-st.com ./deploy/init-letsencrypt.sh
```

Log in at `https://dashboard.cod-st.com` with `APP_ADMIN_EMAIL` /
`APP_ADMIN_INITIAL_PASSWORD` (you'll be forced to change the password).

Full server provisioning, DNS, Postal install, warming, and hardening:
[DEPLOYMENT.md](DEPLOYMENT.md). Day-to-day operations: [OPERATIONS.md](OPERATIONS.md).

## Local development (no infrastructure)

The app defaults to `POSTAL_USE_MOCK=true`. The test suite runs on SQLite +
fakeredis + eager Celery — no Postgres/Redis/Postal needed:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Key correctness rules (do not weaken)

- **One email exists once system-wide** (UNIQUE on normalized email).
- **Global suppression is absolute** — enforced at recipient materialization
  *and* again at per-send time. No campaign ever sends to a suppressed address.
- **Any bounce/complaint → permanent global suppression** (owner policy; the
  decision flows through a single `classify_bounce()` seam).
- **Sending is idempotent** — a per-recipient ledger + Redis lock + DB guard mean
  retries and duplicate enqueues never double-send.
- **Every campaign email must carry an unsubscribe link** — templates without
  `{{unsubscribe_url}}` are refused at schedule time.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — diagram + the sending pipeline in detail
- [DEPLOYMENT.md](DEPLOYMENT.md) — VPS, Postal, DNS, Nginx/certbot, warming, hardening
- [OPERATIONS.md](OPERATIONS.md) — pause/resume, blocklist response, bounce stats
- [DECISIONS.md](DECISIONS.md) — assumptions and trade-offs
