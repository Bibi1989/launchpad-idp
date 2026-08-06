#!/usr/bin/env bash
# Shared CI deploy path for deploy/oci (used by .github/workflows/deploy.yml).
# Usage: deploy/oci/ci-deploy.sh /path/to/oci.env
set -euo pipefail

ENV_FILE="${1:-${COMPOSE_ENV_FILE:-$HOME/.launchpad/oci.env}}"
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/deploy/oci/docker-compose.yml"
MIGRATE_WAIT_SECS="${MIGRATE_WAIT_SECS:-180}"
API_WAIT_ITERS="${API_WAIT_ITERS:-60}"

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

[ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"

PGUSER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
PGUSER="${PGUSER:-launchpad}"
PGDB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
PGDB="${PGDB:-launchpad}"
PGPW="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
[ -n "$PGPW" ] || fail "POSTGRES_PASSWORD missing from $ENV_FILE"

chmod +x "${ROOT_DIR}/deploy/oci/caddy-entrypoint.sh" || true

# 1) Build first so a build failure leaves the running stack untouched.
dc build || fail "docker compose build failed"

# 2) Start Postgres alone and reconcile its role password to the env value.
#    Postgres only honours POSTGRES_PASSWORD on the FIRST init of its data
#    volume, so a rotated password otherwise fails auth for migrate/api
#    (asyncpg InvalidPasswordError). Local socket uses trust, so we can reset
#    it without the old password. Idempotent + safe to repeat every deploy.
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
#    holds Postgres' session-level advisory lock and blocks the next migrate forever
#    (compose `up` then sits until the job times out; diagnostics only show postgres
#    checkpoints).
echo "===== clearing leftover migrate containers ====="
dc stop migrate 2>/dev/null || true
dc rm -f migrate 2>/dev/null || true
# Session-level advisory locks release on disconnect; terminate any orphaned
# backends still waiting on / holding Alembic's lock from a hard-killed migrate.
dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -c \
  "SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE datname = current_database()
     AND pid <> pg_backend_pid()
     AND (query ILIKE '%pg_advisory%' OR application_name ILIKE '%alembic%');" \
  >/dev/null 2>&1 || true

# 4) Run migrations explicitly with a wall-clock timeout (macOS runners often lack
#    GNU `timeout`, so poll the container exit state instead).
echo "===== alembic upgrade head (max ${MIGRATE_WAIT_SECS}s) ====="
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

# 5) Recreate the full stack. migrate is already at head, so the one-shot dependency
#    should complete immediately. api/web/caddy wait on api healthchecks.
dc up -d --remove-orphans || fail "docker compose up failed (a service did not start)"
dc ps

# 6) Wait for the API to report healthy (cold starts: image pulls, k3d warmup).
for i in $(seq 1 "$API_WAIT_ITERS"); do
  if dc exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" 2>/dev/null; then
    echo "API healthy"
    dc ps
    exit 0
  fi
  echo "waiting for API ($i/${API_WAIT_ITERS})..."
  if [ $((i % 6)) -eq 0 ]; then
    echo "===== api status mid-wait ====="
    dc ps api || true
    dc logs --tail=40 api || true
  fi
  sleep 5
done

fail "API did not become healthy within $((API_WAIT_ITERS * 5))s"
