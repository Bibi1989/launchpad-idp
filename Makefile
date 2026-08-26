.PHONY: up down api api-nest api-nest-install api-nest-build worker worker-nest beat web test migrate kind-up kind-down k3s-up k3s-down cluster-up cluster-down oci-up oci-down oci-logs agent-build up-all up-all-nest

# Local cluster engine: k3s (default, via k3d) or kind. Override: LOCAL_K8S_ENGINE=kind make cluster-up
LOCAL_K8S_ENGINE ?= k3s

# Repo-root shared libs (`pkg/`) must be importable when cwd is apps/api.
export PYTHONPATH := $(CURDIR)$(if $(PYTHONPATH),:$(PYTHONPATH),)

up:
	docker compose up -d postgres redis adminer

down:
	docker compose down

migrate:
	cd apps/api && .venv/bin/alembic upgrade head

# Build the hybrid agent image locally. Tag matches settings.agent_image so the
# host installer (/install.sh) finds it without a registry. Override AGENT_IMAGE.
AGENT_IMAGE ?= ghcr.io/launchpad/agent:latest
agent-build:
	docker build -t $(AGENT_IMAGE) agent/

# Applies pending Alembic revisions, then starts the API (same as Docker image entrypoint).
# This is the DEFAULT backend (ACTIVE_BACKEND=fastapi).
api: migrate
	cd apps/api && .venv/bin/uvicorn app.main:app --reload --port 8000

# Alternative NestJS backend (opt-in). Runs on port 8001 alongside FastAPI.
# To point the web app at it: NUXT_PUBLIC_API_BASE=http://localhost:8001/api/v1 make web
api-nest: migrate
	cd apps/api-nest && npm run dev

# One-time install of NestJS backend dependencies.
api-nest-install:
	cd apps/api-nest && npm install

# Build NestJS production bundle.
api-nest-build:
	cd apps/api-nest && npm run build

worker:
	cd apps/api && .venv/bin/celery -A app.workers.celery_app.celery_app worker --loglevel=INFO

# Dev worker that AUTO-RESTARTS on any app/*.py change (needs the dev extra:
# `pip install -e '.[dev]'` in apps/api for watchdog). The plain `worker` target does
# NOT reload, so code fixes to provision/build/deploy only take effect after restart.
worker-dev:
	cd apps/api && .venv/bin/watchmedo auto-restart --directory=./app --pattern='*.py' --recursive -- \
		.venv/bin/celery -A app.workers.celery_app.celery_app worker --loglevel=INFO

# NestJS BullMQ background queue worker.
worker-nest:
	cd apps/api-nest && npm run dev

beat:
	cd apps/api && .venv/bin/celery -A app.workers.celery_app.celery_app beat --loglevel=INFO

web:
	cd apps/web && npm run dev

up-all: up api worker beat test

up-all-nest: up api-nest worker-nest web test

kind-up:
	bash scripts/kind-up.sh

kind-down:
	bash scripts/kind-down.sh

k3s-up:
	bash scripts/k3s-up.sh

k3s-down:
	bash scripts/k3s-down.sh

# Engine-dispatching targets - start/stop whichever local cluster LOCAL_K8S_ENGINE selects.
cluster-up:
	bash scripts/$(LOCAL_K8S_ENGINE)-up.sh

cluster-down:
	bash scripts/$(LOCAL_K8S_ENGINE)-down.sh

# Oracle Cloud Always Free - see deploy/oci/README.md
oci-up:
	@test -f deploy/oci/.env || (echo "Copy deploy/oci/env.example → deploy/oci/.env and edit secrets first." && exit 1)
	docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env up -d --build

oci-down:
	docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env down

oci-logs:
	docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env logs -f

test:
	cd apps/api && .venv/bin/pytest -q
	cd apps/web && npm test
	cd infra/pulumi && npm test
