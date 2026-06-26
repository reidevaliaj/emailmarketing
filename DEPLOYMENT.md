# Deployment

Everything needed to run this on the Contabo VPS, plus the DNS/deliverability
setup the app's correctness depends on. Replace any real value via `.env`; never
commit secrets.

Concrete values used as examples (from the build brief):

| Thing | Value |
|---|---|
| Main server IP | `161.97.160.73` (A record for `dashboard.cod-st.com`) |
| Dedicated sending IP | `89.117.49.213` (gateway `89.117.48.1`, /20) |
| Sending domain | `marketing.cod-st.com` |
| rDNS for sending IP | `mta1.marketing.cod-st.com` |
| Dashboard host | `dashboard.cod-st.com` |

> Do **not** send mail over IPv6 — configure Postal for IPv4-only delivery.

---

## 1. Server provisioning & hardening

The server ships as `root` + password. Do this first.

```bash
# From your workstation: create + copy an SSH key
ssh-keygen -t ed25519 -C "emailmarketing-deploy"
ssh-copy-id root@161.97.160.73

# On the server: create an admin user, then lock down SSH
adduser deploy && usermod -aG sudo deploy
```

Edit `/etc/ssh/sshd_config`:
```
PermitRootLogin prohibit-password
PasswordAuthentication no
Port 2222           # optional: move off 22 (update your firewall + .env notes)
```
`systemctl restart ssh`. **Rotate the initial root password and keep it out of
the repo.**

Firewall (allow only what's needed; outbound 25 for Postal is allowed by
default):
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 2222/tcp     # or 22 if you didn't move it
ufw allow 80,443/tcp
ufw enable
```

Keep the system patched: `apt update && apt upgrade -y` (reboot if the kernel
updated).

### Configure the additional sending IP (Netplan)

Contabo does **not** preconfigure the extra IP and requires it configured within
~4 weeks. Add `89.117.49.213/20` as a secondary address on the primary
interface. Edit the file in `/etc/netplan/` (interface name will vary):

```yaml
network:
  version: 2
  ethernets:
    eth0:
      addresses:
        - 161.97.160.73/24      # existing primary (keep as provisioned)
        - 89.117.49.213/20      # additional sending IP
      routes:
        - to: default
          via: <existing gateway>
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
```

Apply safely (auto-reverts if you lose connectivity):
```bash
netplan try     # confirm within the timeout if connectivity holds
netplan apply
```

---

## 2. Install Docker & run the stack

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/reidevaliaj/emailmarketing.git
cd emailmarketing
cp .env.example .env      # edit thoroughly (see §6)
docker compose up -d --build
```

The one-shot `migrate` service runs `alembic upgrade head` + seeds the admin
user before `app`/`worker`/`beat` start. Check health:
```bash
docker compose ps
docker compose logs -f app worker beat
curl -s http://localhost:8000/healthz   # via app container's exposed port
```

This compose stack is **separate** from the Postal Docker stack (next section).

---

## 3. Install Postal (the mail engine)

Follow the official guide: <https://docs.postalserver.io/getting-started/installation>.
Summary of what matters for us:

1. Install Postal (its own Docker-based stack) on the same VPS.
2. Create an **organization**, a **mail server**, and a **server-level API key**.
   Put the API key + base URL into our `.env` (`POSTAL_API_KEY`,
   `POSTAL_API_URL`) and set `POSTAL_USE_MOCK=false`.
3. **IP pool:** create an IP pool bound to `89.117.49.213` and assign it to the
   mail server so outbound uses the dedicated sending IP.
4. **IPv4-only:** ensure Postal delivers over IPv4 only (do not advertise the
   server's IPv6 for mail).
5. **Webhooks:** add a webhook pointing at
   `https://dashboard.cod-st.com/webhooks/postal?token=<POSTAL_WEBHOOK_SHARED_SECRET>`
   for message events (delivered, bounced, held, spam complaint). Our endpoint
   verifies that token on every request and ignores unverified calls.
6. Generate the DKIM key in Postal and publish it (next section).

---

## 4. DNS & deliverability records

These live outside the codebase but the app's deliverability depends on them.
All on `marketing.cod-st.com` unless noted.

| Record | Host | Value (example) |
|---|---|---|
| **A** (dashboard) | `dashboard.cod-st.com` | `161.97.160.73` |
| **A** (sending host) | `mta1.marketing.cod-st.com` | `89.117.49.213` |
| **PTR / rDNS** | (Contabo panel, for `89.117.49.213`) | `mta1.marketing.cod-st.com` |
| **SPF** (TXT) | `marketing.cod-st.com` | `v=spf1 ip4:89.117.49.213 -all` |
| **DKIM** (TXT) | `<selector>._domainkey.marketing.cod-st.com` | *(public key from Postal)* |
| **DMARC** (TXT) | `_dmarc.marketing.cod-st.com` | `v=DMARC1; p=none; rua=mailto:dmarc@cod-st.com` |
| **MX / return-path** | as Postal requires for bounce processing | *(per Postal setup)* |

Notes:
- **Forward-confirmed reverse DNS (FCrDNS):** the PTR for `89.117.49.213` must
  resolve to `mta1.marketing.cod-st.com`, and that hostname's A record must point
  back to `89.117.49.213`. Set the PTR in the **Contabo control panel**.
- Start DMARC at `p=none` with reports to `dmarc@cod-st.com`; tighten to
  `quarantine`/`reject` later once aligned.
- Keep the **From** domain consistent across all campaigns
  (`*@marketing.cod-st.com`) — the app enforces this at schedule time.

The app runs a **best-effort preflight** at startup (SPF/DKIM/DMARC/MX + PTR for
`SENDING_IPS`) and shows the results on the dashboard. It warns, never blocks.

---

## 5. Nginx + TLS (certbot)

Nginx runs in the compose stack and terminates TLS for `dashboard.cod-st.com`
(config: [`deploy/nginx/dashboard.cod-st.com.conf`](deploy/nginx/dashboard.cod-st.com.conf)).
After the A record points at the server and the stack is up, obtain certs:

```bash
EMAIL=you@cod-st.com ./deploy/init-letsencrypt.sh
# test against LE staging first if you like:  STAGING=1 EMAIL=... ./deploy/init-letsencrypt.sh
```

This stages a temporary self-signed cert so Nginx can boot, runs the certbot
webroot challenge, installs the real certificate, and reloads Nginx. Renewal:
the `certbot` service runs `certbot renew`; Nginx reloads every 6h to pick up
renewed certs. Schedule a periodic `docker compose run --rm certbot` via cron.

The server block already includes the HTTP→HTTPS redirect, the ACME challenge
location, large-upload support (`client_max_body_size 200m` for big CSVs),
websocket/long-poll headers, and sane proxy timeouts.

---

## 6. Pre-flight checklist (before the first send)

- [ ] `.env` filled: secrets generated (`python -c "import secrets;print(secrets.token_urlsafe(48))"`),
      `POSTGRES_URL` points at the `postgres` host, Postal creds set,
      `POSTAL_USE_MOCK=false`, `SENDING_IPS=89.117.49.213`.
- [ ] **Port 25 outbound usable.** Contabo allows it by default but enforces a
      **~25 emails/min** pacing cap. Keep `RATE_GLOBAL_PER_MINUTE` safely under it
      (default **20**). Verify: `nc -zv 89.117.49.213 25` to a known MX, or send a
      Postal test message.
- [ ] **rDNS** for `89.117.49.213` is set in the Contabo panel and FCrDNS checks.
- [ ] **Blocklist check:** verify `89.117.49.213` (and the main IP) on Spamhaus
      etc. before first send (e.g. <https://multirbl.valli.org/>).
- [ ] SPF / DKIM / DMARC resolve (dashboard preflight is green-ish).
- [ ] Send yourself a one-recipient test campaign and confirm headers
      (SPF=pass, DKIM=pass, correct From, working unsubscribe).

---

## 7. Warming schedule (B2B)

New IPs must be warmed or recipients throttle/defer you.

| Phase | Daily volume per IP | Notes |
|---|---|---|
| Days 1–3 | 1,000–2,000 | Send the **most engaged / cleanest** contacts first |
| Days 4–7 | ~double every 2–3 days | Watch bounce/deferral rates; back off if they rise |
| Weeks 2–3 | ramp to full | Reach full ~10k/day by week 2–3 |

Enforce the cap so it can't be exceeded by accident: set `PER_IP_DAILY_CAP` to
the current phase's number and raise it as you warm. At ~20/min the daily target
sends in a few hours, and the forced pacing itself helps deliverability.

A second additional IP is recommended later for pool isolation/rotation (order
from the Contabo panel; add its A + PTR and extend `SENDING_IPS`).

---

## 8. Operating the stack

```bash
docker compose ps                 # status
docker compose logs -f worker     # follow sends/verification
docker compose restart app        # restart a service
docker compose pull && docker compose up -d --build   # update after git pull
docker compose run --rm migrate   # re-run migrations manually if needed
```

Backups: snapshot the `postgres-data` volume (and Postal's data) regularly. Day-
to-day runbook (pause/resume, blocklist response, reading bounce stats):
[OPERATIONS.md](OPERATIONS.md).
