#!/usr/bin/env bash
# Bootstrap Let's Encrypt certs for the dashboard, then start serving HTTPS.
#
# Why this exists: nginx won't start if its ssl_certificate path is missing, but
# certbot's HTTP-01 challenge needs nginx already serving :80. We break the
# chicken-and-egg by staging a throwaway self-signed cert, starting nginx, then
# replacing it with a real Let's Encrypt cert via the webroot challenge.
#
# Run once on the server after DNS for the domain points at this host:
#   DOMAIN=dashboard.cod-st.com EMAIL=you@cod-st.com ./deploy/init-letsencrypt.sh
set -euo pipefail

DOMAIN="${DOMAIN:-dashboard.cod-st.com}"
EMAIL="${EMAIL:?Set EMAIL=you@cod-st.com}"
STAGING="${STAGING:-0}"   # set STAGING=1 to test against LE staging first

CONF_DIR="./deploy/certbot/conf"
WWW_DIR="./deploy/certbot/www"
LIVE="$CONF_DIR/live/$DOMAIN"

mkdir -p "$LIVE" "$WWW_DIR"

echo "==> Staging a temporary self-signed cert so nginx can boot"
docker compose run --rm --entrypoint "\
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout '/etc/letsencrypt/live/$DOMAIN/privkey.pem' \
    -out '/etc/letsencrypt/live/$DOMAIN/fullchain.pem' \
    -subj '/CN=$DOMAIN'" certbot

echo "==> Starting nginx"
docker compose up -d nginx

echo "==> Deleting the temporary cert and requesting the real one"
docker compose run --rm --entrypoint "rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

STAGING_FLAG=""
[ "$STAGING" != "0" ] && STAGING_FLAG="--staging"

docker compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    $STAGING_FLAG --email $EMAIL --agree-tos --no-eff-email \
    -d $DOMAIN --rsa-key-size 4096 --force-renewal" certbot

echo "==> Reloading nginx with the real certificate"
docker compose exec nginx nginx -s reload

echo "Done. https://$DOMAIN should now be live."
echo "Renewal: the certbot service runs 'certbot renew'; schedule it via cron or"
echo "run 'docker compose run --rm certbot' periodically (nginx reloads every 6h)."
