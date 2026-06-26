# Decisions & assumptions

Where the brief was ambiguous I chose the safer/simpler option and recorded it
here, plus things worth flagging that affect correctness or deliverability.

## Architecture / stack

1. **Server-rendered UI (Jinja2), not a separate SPA.** The brief left the
   frontend to the builder and said keep it simple. One deployable, one language,
   no Node build step — lowest operating cost and maintenance. JSON endpoints
   still exist (status API) so a SPA could be added later.
2. **Contacts use a single `list_id` FK, not a join table.** The hard rule "an
   email may not appear in two lists" makes a many-to-many membership table
   semantically wrong. One global-unique contact → one list.
3. **Deleting a list deletes its contacts** (a list = a CSV upload). This frees
   those emails for future re-import; **suppressions persist independently**, so
   deletion never resurrects a suppressed address.
4. **Dual DB setup: async (asyncpg) for FastAPI, sync (psycopg) for Celery.**
   Honors the brief's "FastAPI async" while avoiding asyncpg-across-event-loops
   fragility in tasks. Models are shared. Small duplication in the few functions
   used by both sides (suppression, config) is the price.
5. **Portable models (JSONB on Postgres, JSON on SQLite).** Lets the critical-
   path test suite run with zero infrastructure. **Production must use Postgres**
   (JSONB indexing, real concurrency); SQLite is tests/dev only.

## Sending / pacing

6. **Daily cap uses check-then-record**, not reserve-then-rollback. Concurrent
   workers may overshoot the per-IP daily cap by at most (concurrency − 1), which
   is immaterial for a warming safety net and keeps the code simple.
7. **Rate limiter is a Redis WATCH/MULTI token bucket (no Lua).** Atomic
   all-or-nothing acquisition across the global + per-domain buckets, and it runs
   identically on fakeredis in tests (no Lua/lupa dependency).
8. **Pacing re-enqueues a fresh task** instead of using Celery's retry, so being
   rate-limited never consumes the transient-failure retry budget.
9. **"Send now" dispatches via Beat (≤ ~60s)**, not immediately from the web
   request. This avoids a commit-before-dispatch race and is instant enough for
   email. All dispatch flows through one path (Beat).
10. **One recipient per Postal API call.** Gives a clean per-recipient ledger and
    idempotency; batching is unnecessary at ~20/min.
11. **`ip_pool` is a logical label** (passed to Postal as a tag and used for the
    daily cap). Postal assigns the actual sending IP/pool at the server/
    credential level, so per-message IP routing is configured **in Postal**, not
    here.

## Import / verification

12. **CSV import and verification run as Celery tasks**, not in the upload
    request, so large files never block the UI.
13. **On global-duplicate import we skip the row and do NOT update name fields**
    of the existing contact. The brief said updating is optional; we avoid
    surprising mutations of existing data.
14. **Invalid (no-MX) contacts get contact status `bounced`** + `verification_
    result=invalid` + global suppression. The contact-status enum has no
    dedicated `invalid` value; `bounced` cleanly excludes them from sending. Both
    the status filter and suppression prevent any send.
15. **Layers 3 & 4 verification intentionally NOT built.** Role/disposable
    filtering (L3) would remove the owner's intended `info@` B2B targets; SMTP
    mailbox probing (L4) would risk our sending IPs. A `VerificationProvider`
    seam is left for an external API later.

## Compliance / security

16. **Webhook verification uses the shared secret** the brief provisioned —
    matched via HMAC header, secret header, or `?token=` URL param (constant-
    time). Postal's *native* signing is RSA; a `verify_rsa_signature` seam is
    left to swap in public-key verification later. **Recommended:** configure
    Postal's webhook URL with `?token=<secret>` over HTTPS.
17. **Unsubscribe tokens are non-expiring signed emails** (itsdangerous).
    `UNSUBSCRIBE_SECRET` must stay stable across deploys or old links break.
18. **CSRF:** we rely on `SameSite=Lax` session cookies rather than per-form CSRF
    tokens. Acceptable for an internal single-admin tool; **flagged** as a
    hardening option (add `starlette-csrf` if multi-user/exposed).
19. **Auth:** signed session cookie for the UI; SHA-256-hashed static API keys for
    the status API. `JWT_SECRET` is reserved but unused today.
20. **HTML emails send `html_body` only** (no auto-generated plain-text part) to
    stay basic; all merge **values** are HTML-escaped. Adding a plain-text
    alternative is a reasonable future deliverability tweak.
21. **Open/click tracking not built** (per brief). The `email_events` table is
    flexible enough to add it later.

## Flagged — things that affect correctness / deliverability

- **Contabo's ~25 emails/min cap is the binding constraint.** Default global
  rate is 20/min. ~10k/day fits in ~8.3h, so a 10k campaign won't all go out at
  once — schedule with that in mind. Raise `RATE_GLOBAL_PER_MINUTE` only if the
  provider cap changes.
- **The any-bounce → suppress policy is aggressive by design** (soft/temporary
  bounces suppress too). That's the owner's stated policy; relax it in the single
  `classify_bounce()` seam if ever desired.
- **"External forwarding blocked" NDRs** (e.g. Microsoft `550 5.7.520`) mean the
  message *was* delivered; they land in a human inbox, not Postal's return-path,
  so Postal generally won't fire a bounce webhook — the aggressive rule won't
  wrongly suppress them. We deliberately built nothing that scrapes a human inbox.
- **Forward-confirmed rDNS is mandatory** for B2B deliverability: PTR for the
  sending IP → `mta1.marketing.cod-st.com`, and that A record → the same IP.
- **Rotate every secret** from the `.env.example` placeholders before going live;
  the seeded admin password is force-changed on first login.
- **Postal must deliver IPv4-only** — the server's IPv6 should not be used for
  mail (treated more strictly by recipients).
