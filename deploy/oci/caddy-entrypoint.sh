#!/bin/sh
# Sanitize LAUNCHPAD_SITE_ADDRESS before Caddy starts.
# Invalid values (empty, http(s) URL, bare IP, localhost) crash-loop or HTTPS-redirect
# localhost checks - force plain :80 for those cases.

set -eu

ADDR="${LAUNCHPAD_SITE_ADDRESS:-:80}"
ADDR="$(printf '%s' "$ADDR" | tr -d '[:space:]')"

case "$ADDR" in
"" | http://* | https://*)
  echo "caddy-entrypoint: invalid LAUNCHPAD_SITE_ADDRESS='${LAUNCHPAD_SITE_ADDRESS:-}'; using :80"
  ADDR=":80"
  ;;
localhost | localhost:* | Localhost | LOCALHOST)
  echo "caddy-entrypoint: localhost site address enables HTTPS redirects; using :80"
  ADDR=":80"
  ;;
esac

# Bare IPv4 → listen on :80 (Let's Encrypt needs a DNS name, not a raw IP).
if printf '%s' "$ADDR" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(:[0-9]+)?$'; then
  echo "caddy-entrypoint: bare IP '${ADDR}' is not a valid Caddy site address; using :80"
  ADDR=":80"
fi

# Hostname without scheme is OK (e.g. launchpad.example.com) when auto_https is enabled.
export LAUNCHPAD_SITE_ADDRESS="$ADDR"
echo "caddy-entrypoint: LAUNCHPAD_SITE_ADDRESS=${LAUNCHPAD_SITE_ADDRESS}"

if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
  echo "caddy-entrypoint: Caddyfile validation failed" >&2
  exit 1
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
