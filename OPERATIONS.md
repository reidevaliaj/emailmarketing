# Operations runbook

Day-to-day operation of the running system. Assumes the Docker stack is up (see
[DEPLOYMENT.md](DEPLOYMENT.md)).

## Pause / resume / cancel a campaign

From the campaign detail page (`/campaigns/{id}`):

- **Pause** — stops new sends being picked up. In-flight tasks finish; remaining
  recipients stay `pending`. Safe to do mid-send.
- **Resume** — re-enqueues the remaining `pending` recipients (re-materialization
  is idempotent, so nothing is sent twice). Counters continue from where they
  were.
- **Cancel** — terminal; the campaign will not send further. Pending recipients
  simply never send.

The recipient **ledger** on that page shows each address's status
(`pending/sent/delivered/bounced/failed/skipped_suppressed`), its Postal message
id, last event time, and any error. Counters auto-refresh while `sending`.

CLI equivalents if the UI is unavailable:
```bash
# pause: set status; the worker stops picking up this campaign's sends
docker compose exec app python -c "import asyncio; ..."   # prefer the UI
```
(The UI is the supported path; avoid hand-editing statuses.)

## When a sending IP gets blocklisted

1. **Stop the bleeding.** Pause active campaigns (or set
   `RATE_GLOBAL_PER_MINUTE` very low and restart `worker`/`beat`).
2. **Confirm** which list and why: check the IP on Spamhaus / multirbl
   (<https://multirbl.valli.org/>). Note the listing reason.
3. **Fix the cause** before delisting:
   - High bounce rate? The dashboard shows it. Our any-bounce→suppress policy
     should keep lists clean — investigate the source list quality and re-verify.
   - Spam complaints? Review recent campaign content/targeting.
   - Sending too fast / not warmed? Lower `PER_IP_DAILY_CAP` and
     `RATE_GLOBAL_PER_MINUTE`; resume warming slowly.
4. **Request delisting** with the blocklist operator once fixed.
5. If you have a second sending IP, you can shift the campaign's **IP pool**
   while remediating; otherwise wait out the delisting.
6. Resume at a **reduced** rate and watch the dashboard.

Rate/cap changes take effect after restarting the worker:
```bash
# edit .env (RATE_GLOBAL_PER_MINUTE / RATE_PER_DOMAIN_PER_MINUTE / PER_IP_DAILY_CAP)
docker compose up -d worker beat
```

## Reading bounce stats

**Dashboard (`/`):** sent / delivered / bounced / failed totals, current
**bounce rate** (bounced ÷ sent), global **suppression size**, and **per-IP
volume today vs the daily cap**. A bounce rate creeping up is the earliest
warning sign — pause and investigate the source list.

**Status API (for external monitoring):** create a key under Settings, then:
```bash
curl -H "X-API-Key: emk_xxx" https://dashboard.cod-st.com/api/status
curl -H "X-API-Key: emk_xxx" https://dashboard.cod-st.com/api/campaigns/42
```
Returns JSON totals, bounce rate, suppression size, per-pool volume, and recent
campaign progress. (`spam_score` is a documented stub — no paid service wired in.)

What the numbers mean:
- **sent** = handed to Postal successfully (not yet confirmation of delivery).
- **delivered / bounced** = from Postal webhooks (downstream of sent).
- **failed** = Postal rejected the API call (a permanent error) — *not* a bounce.
- **skipped_suppressed** = excluded because the address was suppressed.

## Remove / suppress an address (converted, replied, complained)

Use **Search** (`/search`): look up any address across the whole system by full
address, partial text, or domain. "Remove & suppress" deletes the contact row
**and** adds the email to global suppression so it can never be re-imported or
re-sent. You can also suppress an address that has no contact row.

Unsubscribes (link or one-click) and any bounce/complaint do this automatically.

## Adjusting the import filters

**Free-provider blocklist** (Settings → Free-provider filter): toggle on/off and
edit the domain patterns (`gmail.com` exact, `hotmail.` prefix, `gmx.*` prefix).
Defaults ON. Per-upload, you can also untick the filter for one import.

## Routine checks

- `docker compose ps` — all services healthy.
- `docker compose logs -f worker` — watch sends/verification; transient retries
  are normal, repeated permanent failures are not.
- Dashboard bounce rate trend after each campaign.
- Disk usage of the `postgres-data` volume; back it up regularly.
- Certbot renewal (`docker compose run --rm certbot`) — schedule via cron.
