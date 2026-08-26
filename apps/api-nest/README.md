# Launchpad API (NestJS)

An alternative control-plane backend written in NestJS, built to reach **parity with the
FastAPI app** (`apps/api`). It serves the same `/api/v1` contract and validates the same
JWTs, so the web app can talk to either backend without changes.

FastAPI remains the default (`ACTIVE_BACKEND=fastapi`). This app is opt-in.

## Why these packages (FastAPI -> NestJS)

| FastAPI stack        | NestJS equivalent used here          |
| -------------------- | ------------------------------------ |
| FastAPI + Uvicorn    | `@nestjs/core` + `@nestjs/platform-fastify` |
| Pydantic             | `class-validator` + `class-transformer` |
| python-jose / JWT    | `@nestjs/jwt`                        |
| Settings/env         | `@nestjs/config`                     |
| structlog            | `nestjs-pino`                        |
| OpenAPI docs         | `@nestjs/swagger` (served at `/docs`)|

Planned for later modules: **Prisma** (SQLAlchemy), **Prisma Migrate** (Alembic),
**BullMQ** (`@nestjs/bullmq`, for Celery), **@aws-sdk/**\*** (boto3),
**@kubernetes/client-node** (k8s), **@nestjs/schedule** (Celery beat).

## Run it

```bash
cd apps/api-nest
cp .env.example .env      # keep JWT_SECRET identical to the FastAPI .env
npm install
npm run dev               # starts on http://localhost:8001, docs at /docs
```

Or from the repo root: `make api-nest`.

## Point the web app at it

The web app chooses its backend via one env var (FastAPI is the default):

```bash
# Use NestJS instead of FastAPI:
NUXT_PUBLIC_API_BASE=http://localhost:8001/api/v1 make web
```

Leave `NUXT_PUBLIC_API_BASE` unset (or `/api/v1`) to keep using FastAPI on :8000.

## What is ported so far

- **App skeleton**: Fastify, config, pino logging, Swagger, global `/api/v1` prefix.
- **Auth**: `JwtAuthGuard` verifies the same HS256 tokens (shared `JWT_SECRET`, issuer
  `launchpad-idp`); `@AuthUser()` injects the decoded user.
- **cloud-providers module** (first vertical slice), identical JSON to FastAPI:
  - `GET /api/v1/cloud-providers`
  - `GET /api/v1/cloud-providers/:id`  (includes `services`)
  - `GET /api/v1/cloud-providers/:id/services`
  - `GET /api/v1/cloud-providers/:id/tools`
  - `GET /api/v1/provisioning-tools`

## Structure (readable by design)

```
src/
  main.ts                       # bootstrap: Fastify + prefix + Swagger + CORS
  app.module.ts                 # wires config, logging, auth, feature modules
  config/configuration.ts       # env -> typed AppConfig
  common/auth/                  # JwtAuthGuard, @AuthUser(), CurrentUser
  cloud-providers/
    cloud-providers.controller.ts
    cloud-providers.service.ts
    cloud-providers.types.ts
    data/                       # the catalog / tools / services data, ported 1:1
```

Each new FastAPI router becomes one NestJS *module* (controller + service + data),
following the pattern in `cloud-providers/`.
