.PHONY: up down api worker beat web test migrate kind-up kind-down k3s-up k3s-down cluster-up cluster-down oci-up oci-down oci-logs

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

# Applies pending Alembic revisions, then starts the API (same as Docker image entrypoint).
api: migrate
	cd apps/api && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:
	cd apps/api && .venv/bin/celery -A app.workers.celery_app.celery_app worker --loglevel=INFO

beat:
	cd apps/api && .venv/bin/celery -A app.workers.celery_app.celery_app beat --loglevel=INFO

web:
	cd apps/web && npm run dev

up-all: up api worker beat web test

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
