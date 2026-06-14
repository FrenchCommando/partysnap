#!/usr/bin/env bash
# Generate .env with random secrets. You supply the instance values as env vars
# (all three required — the script errors if any is missing):
#
#   PARTYSNAP_DOMAIN=snap.example.com APP_PORT=8080 ADMIN_HANDLE=martial bash scripts/init-env.sh
#
# Refuses to overwrite an existing .env (your secrets persist with the DB volume).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists — refusing to overwrite. Remove it first to regenerate." >&2
  exit 1
fi

domain="${PARTYSNAP_DOMAIN:?set PARTYSNAP_DOMAIN (your TLS domain); e.g. PARTYSNAP_DOMAIN=snap.example.com}"
port="${APP_PORT:?set APP_PORT (the host port nginx proxies to; any free port, NOT the container's internal 8000); e.g. APP_PORT=8080}"
handle="${ADMIN_HANDLE:?set ADMIN_HANDLE (your admin login name); e.g. ADMIN_HANDLE=martial}"
app_secret="$(openssl rand -base64 32)"
pg_password="$(openssl rand -hex 24)"          # hex => URL-safe (goes in DATABASE_URL)
admin_password="$(openssl rand -base64 12)"    # single-use; changed on first login

cat > .env <<EOF
PARTYSNAP_DOMAIN=${domain}
APP_PORT=${port}
APP_SECRET=${app_secret}

POSTGRES_USER=partysnap
POSTGRES_PASSWORD=${pg_password}
POSTGRES_DB=partysnap

ADMIN_HANDLE=${handle}
ADMIN_PASSWORD=${admin_password}

# Convenience mode (optional) — fill in to enable Google Photos:
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
EOF

chmod 600 .env

# Render the nginx proxy conf with this instance's domain + port (single source).
sed -e "s/__DOMAIN__/${domain}/" -e "s/__PORT__/${port}/" \
    deploy/nginx/partysnap.conf.template > deploy/nginx/partysnap.conf

echo "Wrote .env (secrets generated) + deploy/nginx/partysnap.conf (server_name ${domain}, port ${port})."
echo "  admin handle:   ${handle}"
echo "  admin password: ${admin_password}   (change on first login)"
echo "Set PARTYSNAP_DOMAIN / ADMIN_HANDLE via env vars when running this if the defaults are wrong."
