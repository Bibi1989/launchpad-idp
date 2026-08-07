#!/usr/bin/env bash
# Shared CI deploy path for deploy/oci (used by .github/workflows/deploy.yml).
# Usage: deploy/oci/ci-deploy.sh /path/to/oci.env
#
# Rolling cutover: build while the old stack keeps serving, migrate, then recreate
# api → web → workers one at a time so Caddy keeps the site reachable as long as
# possible. Brief gaps still happen while a single container restarts.
set -euo pipefail

ENV_FILE="${1:-${COMPOSE_ENV_FILE:-$HOME/.launchpad/oci.env}}"
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/deploy/oci/docker-compose.yml"
MIGRATE_WAIT_SECS="${MIGRATE_WAIT_SECS:-180}"
API_WAIT_ITERS="${API_WAIT_ITERS:-60}"
WEB_WAIT_ITERS="${WEB_WAIT_ITERS:-36}"

cd "$ROOT_DIR"

dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

dump_diagnostics() {
  echo "===== docker compose ps (all) ====="
  dc ps -a || true
  echo "===== api logs (tail 200) ====="
  dc logs --tail=200 api || true
  echo "===== migrate logs (tail 200) ====="
  dc logs --tail=200 migrate || true
  echo "===== worker logs (tail 100) ====="
  dc logs --tail=100 worker || true
  echo "===== web logs (tail 80) ====="
  dc logs --tail=80 web || true
  echo "===== caddy logs (tail 40) ====="
  dc logs --tail=40 caddy || true
  echo "===== postgres/redis (ps only; full postgres logs omit noise) ====="
  dc ps postgres redis || true
}

fail() {
  echo "DEPLOY FAILED: $1"
  dump_diagnostics
  exit 1
}

wait_api_healthy() {
  local label="${1:-API}"
  for i in $(seq 1 "$API_WAIT_ITERS"); do
    if dc exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" 2>/dev/null; then
      echo "${label} healthy"
      return 0
    fi
    echo "waiting for ${label} ($i/${API_WAIT_ITERS})..."
    if [ $((i % 6)) -eq 0 ]; then
      echo "===== api status mid-wait ====="
      dc ps api || true
      dc logs --tail=40 api || true
    fi
    sleep 5
  done
  return 1
}

wait_web_ready() {
  for i in $(seq 1 "$WEB_WAIT_ITERS"); do
    if dc exec -T web node -e "fetch('http://127.0.0.1:3000/').then((r)=>process.exit(r.ok||r.status===404?0:1)).catch(()=>process.exit(1))" 2>/dev/null; then
      echo "web ready"
      return 0
    fi
    echo "waiting for web ($i/${WEB_WAIT_ITERS})..."
    if [ $((i % 6)) -eq 0 ]; then
      dc ps web || true
      dc logs --tail=30 web || true
    fi
    sleep 5
  done
  return 1
}

[ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"

PGUSER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
PGUSER="${PGUSER:-launchpad}"
PGDB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
PGDB="${PGDB:-launchpad}"
PGPW="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
[ -n "$PGPW" ] || fail "POSTGRES_PASSWORD missing from $ENV_FILE"

chmod +x "${ROOT_DIR}/deploy/oci/caddy-entrypoint.sh" || true

# 1) Build first so a build failure leaves the running stack untouched (site stays up).
echo "===== build images (old stack keeps serving) ====="
dc build || fail "docker compose build failed"

# 2) Ensure Postgres/Redis without bouncing healthy instances when possible.
dc up -d postgres || fail "postgres failed to start"
reconciled=0
for i in $(seq 1 30); do
  if echo "ALTER USER \"$PGUSER\" WITH PASSWORD :'pw';" \
       | dc exec -T postgres psql -v ON_ERROR_STOP=1 -v pw="$PGPW" -U "$PGUSER" -d "$PGDB" >/dev/null 2>&1; then
    reconciled=1
    break
  fi
  echo "waiting for postgres before reconciling password ($i/30)..."
  sleep 2
done
[ "$reconciled" = 1 ] || fail "could not reconcile the postgres role password"

dc up -d redis || fail "redis failed to start"

# 3) Clear any stuck one-shot migrate container. A previous hung `alembic upgrade`
#    holds Postgres' session-level advisory lock and blocks the next migrate forever.
echo "===== clearing leftover migrate containers ====="
dc stop migrate 2>/dev/null || true
dc rm -f migrate 2>/dev/null || true
dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -c \
  "SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE datname = current_database()
     AND pid <> pg_backend_pid()
     AND (query ILIKE '%pg_advisory%' OR application_name ILIKE '%alembic%');" \
  >/dev/null 2>&1 || true

# 4) Migrate while the OLD api/web/caddy keep serving.
echo "===== alembic upgrade head (max ${MIGRATE_WAIT_SECS}s; old stack still serving) ====="
dc up -d --no-deps migrate || fail "could not start migrate service"

migrate_done=0
migrate_rc=1
for i in $(seq 1 "$MIGRATE_WAIT_SECS"); do
  cid="$(dc ps -aq migrate 2>/dev/null | head -1 || true)"
  if [ -n "$cid" ]; then
    running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)"
    if [ "$running" != "true" ]; then
      migrate_rc="$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null || echo 1)"
      migrate_done=1
      break
    fi
  fi
  if [ $((i % 10)) -eq 0 ]; then
    echo "waiting for migrate ($i/${MIGRATE_WAIT_SECS})..."
    dc logs --tail=20 migrate || true
  fi
  sleep 1
done

if [ "$migrate_done" != 1 ]; then
  echo "===== migrate still running - dumping logs and killing ====="
  dc logs --tail=100 migrate || true
  dc stop migrate 2>/dev/null || true
  dc rm -f migrate 2>/dev/null || true
  fail "alembic upgrade head did not finish within ${MIGRATE_WAIT_SECS}s (often an advisory-lock hang)"
fi

echo "===== migrate finished (exit=${migrate_rc}) ====="
dc logs --tail=50 migrate || true
[ "$migrate_rc" = "0" ] || fail "alembic upgrade head failed (exit ${migrate_rc})"

# 5) Rolling cutover: recreate one service at a time so Caddy keeps routing to
#    whatever is still up. --no-deps avoids yanking the whole dependency tree.
echo "===== rolling recreate: api ====="
dc up -d --no-deps --force-recreate api || fail "api recreate failed"
wait_api_healthy "api" || fail "API did not become healthy within $((API_WAIT_ITERS * 5))s after recreate"

echo "===== rolling recreate: web ====="
dc up -d --no-deps --force-recreate web || fail "web recreate failed"
wait_web_ready || fail "web did not become ready within $((WEB_WAIT_ITERS * 5))s after recreate"

echo "===== rolling recreate: worker + beat ====="
dc up -d --no-deps --force-recreate worker beat || fail "worker/beat recreate failed"

# Caddy: pick up config/image changes without a hard bounce when unchanged.
# Prefer recreate only when compose detects a diff; avoid --force-recreate so the
# public edge stays up across most deploys.
echo "===== ensure caddy ====="
dc up -d --no-deps caddy || fail "caddy failed to start"

# Start any missing services / drop orphans without bouncing the healthy stack.
dc up -d --remove-orphans --no-recreate || fail "compose reconcile failed"
dc ps

wait_api_healthy "api (final)" || fail "API did not stay healthy after rolling cutover"
echo "Rolling deploy complete - site served throughout build; brief gaps only during api/web recreate"
dc ps
exit 0
