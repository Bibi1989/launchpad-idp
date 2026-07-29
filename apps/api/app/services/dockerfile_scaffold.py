"""Detect project stack and scaffold hardened multi-stage Dockerfiles."""

from __future__ import annotations

import re
from typing import Final

from app.schemas.dockerfile_schema import ProjectStack

_STACK_MARKERS: Final[dict[ProjectStack, tuple[str, ...]]] = {
    ProjectStack.NODE: (
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
    ),
    ProjectStack.PYTHON: (
        "pyproject.toml",
        "requirements.txt",
        "Pipfile",
        "poetry.lock",
        "setup.py",
    ),
    ProjectStack.GO: ("go.mod", "go.sum"),
    ProjectStack.JAVA: (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    ),
    ProjectStack.RUST: ("Cargo.toml", "Cargo.lock"),
}

_DEFAULT_PATH = "dockers/Dockerfile"


def detect_stack(root_paths: set[str] | list[str]) -> tuple[ProjectStack, list[str]]:
    """Return the best-matching stack and the marker filenames that matched."""
    normalized = [_normalize_repo_path(p) for p in root_paths if p.strip()]
    names = {p.split("/")[-1] for p in normalized}
    # Prefer exact root markers (files at repository root).
    root_names = {p for p in normalized if "/" not in p}
    for stack, markers in _STACK_MARKERS.items():
        hits = [m for m in markers if m in root_names or m in names]
        if hits:
            return stack, sorted(hits)
    return ProjectStack.UNKNOWN, []


def _normalize_repo_path(path: str) -> str:
    cleaned = path.strip().removeprefix("./")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def scaffold_dockerfile(
    stack: ProjectStack,
    *,
    app_name: str = "app",
    listen_port: int = 8080,
) -> str:
    """Return a production-grade multi-stage Dockerfile for the given stack."""
    safe_name = _sanitize_name(app_name)
    port = listen_port
    generators = {
        ProjectStack.REACT_VITE: _react_vite_dockerfile,
        ProjectStack.NEXTJS: _nextjs_dockerfile,
        ProjectStack.NUXTJS: _react_vite_dockerfile,
        ProjectStack.VUEJS: _react_vite_dockerfile,
        ProjectStack.SVELTE: _react_vite_dockerfile,
        ProjectStack.FASTAPI: _fastapi_dockerfile,
        ProjectStack.FLASK: _python_dockerfile,
        ProjectStack.DJANGO: _python_dockerfile,
        ProjectStack.EXPRESS: _node_dockerfile,
        ProjectStack.NESTJS: _nestjs_dockerfile,
        ProjectStack.SPRINGBOOT: _springboot_dockerfile,
        ProjectStack.NODE: _node_dockerfile,
        ProjectStack.PYTHON: _python_dockerfile,
        ProjectStack.GO: _go_dockerfile,
        ProjectStack.JAVA: _java_dockerfile,
        ProjectStack.RUST: _rust_dockerfile,
        ProjectStack.GENERIC: _generic_dockerfile,
        ProjectStack.UNKNOWN: _generic_dockerfile,
    }
    # Fallback lookup if stack is string
    gen = generators.get(stack) or generators.get(ProjectStack(str(stack)), _generic_dockerfile)
    return gen(safe_name, port)


def default_dockerfile_path() -> str:
    return _DEFAULT_PATH


def scaffold_docker_compose(
    app_name: str = "app",
    listen_port: int = 8080,
    dockerfile_path: str = "dockers/Dockerfile",
) -> str:
    """Return a production-ready docker-compose.yml file for the application."""
    safe_name = _sanitize_name(app_name)
    return f"""services:
  {safe_name}:
    build:
      context: .
      dockerfile: {dockerfile_path}
    image: ${{APP_IMAGE:-{safe_name}:latest}}
    container_name: {safe_name}
    ports:
      - "{listen_port}:{listen_port}"
    environment:
      - PORT={listen_port}
      - NODE_ENV=production
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:{listen_port}/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
"""


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()) or "app"
    return cleaned[:64]


def _common_footer(*, port: int) -> str:
    return f"""\
USER 10001:10001
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:{port}/health || exit 1
"""


def _node_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Node.js image (non-root USER 10001).

FROM node:22-alpine AS deps
WORKDIR /src
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f yarn.lock ]; then yarn install --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi

FROM node:22-alpine AS build
WORKDIR /src
COPY --from=deps /src/node_modules ./node_modules
COPY . .
ENV NODE_ENV=production
RUN \\
  if [ -f package.json ] && grep -q '"build"' package.json; then \\
    npm run build || yarn build || pnpm build; \\
  else echo "no build script"; fi

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src ./
ENV NODE_ENV=production
ENV PORT={port}
{_common_footer(port=port)}CMD ["node", "server.js"]
# Image: {app_name}
"""


def _python_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Python image (non-root USER 10001).

FROM python:3.12-alpine AS builder
WORKDIR /src
RUN apk add --no-cache build-base libffi-dev
COPY requirements.txt pyproject.toml poetry.lock* ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN \\
  if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; \\
  elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; \\
  else pip install --no-cache-dir uvicorn fastapi; fi

FROM python:3.12-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT={port}
{_common_footer(port=port)}CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
# Image: {app_name}
"""


def _go_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Go image (non-root USER 10001).

FROM golang:1.23-alpine AS build
WORKDIR /src
RUN apk add --no-cache git ca-certificates
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/{app_name} .

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /out/{app_name} /app/{app_name}
ENV PORT={port}
{_common_footer(port=port)}CMD ["/app/{app_name}"]
# Image: {app_name}
"""


def _java_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Java image (non-root USER 10001).

FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f mvnw ]; then chmod +x mvnw && ./mvnw -q -DskipTests package; \\
  elif [ -f gradlew ]; then chmod +x gradlew && ./gradlew -q bootJar; \\
  elif [ -f pom.xml ]; then mvn -q -DskipTests package; \\
  else echo "No Maven/Gradle build found" && exit 1; fi

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/target/*.jar /app/app.jar
ENV PORT={port}
{_common_footer(port=port)}CMD ["java", "-jar", "/app/app.jar"]
# Image: {app_name}
"""


def _rust_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Rust image (non-root USER 10001).

FROM rust:1.83-alpine AS build
WORKDIR /src
RUN apk add --no-cache musl-dev
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo "fn main() {{}}" > src/main.rs && cargo build --release && rm -rf src
COPY . .
RUN cargo build --release

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/target/release/{app_name} /app/{app_name}
ENV PORT={port}
{_common_footer(port=port)}CMD ["/app/{app_name}"]
# Image: {app_name}
"""


def _generic_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage generic image (non-root USER 10001).
# Replace the build stage with your language toolchain.

FROM alpine:3.21 AS build
WORKDIR /src
COPY . .
RUN echo "Customize this build stage for your stack"

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src /app
ENV PORT={port}
{_common_footer(port=port)}CMD ["sleep", "infinity"]
# Image: {app_name}
"""


def _fastapi_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage FastAPI image (non-root USER 10001).

FROM python:3.12-alpine AS builder
WORKDIR /src
RUN apk add --no-cache build-base libffi-dev
COPY requirements.txt pyproject.toml poetry.lock* ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN \\
  if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; \\
  elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; \\
  else pip install --no-cache-dir uvicorn fastapi pydantic; fi

FROM python:3.12-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT={port}
{_common_footer(port=port)}CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
# Image: {app_name}
"""


def _react_vite_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage React (Vite) image with non-root Nginx.

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM nginxinc/nginx-unprivileged:alpine AS runtime
COPY --from=build --chown=101:101 /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
# Image: {app_name}
"""


def _nextjs_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Next.js image (standalone mode, USER 10001).

FROM node:22-alpine AS deps
WORKDIR /src
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* pnpm-lock.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; else npm ci; fi

FROM node:22-alpine AS builder
WORKDIR /src
COPY --from=deps /src/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S -G nextjs nextjs \\
  && apk add --no-cache ca-certificates wget
COPY --from=builder /src/public ./public
COPY --from=builder --chown=10001:10001 /src/.next/standalone ./
COPY --from=builder --chown=10001:10001 /src/.next/static ./.next/static
ENV NODE_ENV=production PORT={port}
{_common_footer(port=port)}CMD ["node", "server.js"]
# Image: {app_name}
"""


def _nestjs_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage NestJS image (non-root USER 10001).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* pnpm-lock.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; else npm ci; fi
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/dist ./dist
COPY --from=build --chown=10001:10001 /src/node_modules ./node_modules
COPY --from=build --chown=10001:10001 /src/package.json ./package.json
ENV NODE_ENV=production PORT={port}
{_common_footer(port=port)}CMD ["node", "dist/main.js"]
# Image: {app_name}
"""


def _springboot_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Spring Boot image (non-root USER 10001).

FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f mvnw ]; then chmod +x mvnw && ./mvnw -q -DskipTests clean package; \\
  elif [ -f gradlew ]; then chmod +x gradlew && ./gradlew -q bootJar; \\
  else mvn -q -DskipTests package; fi

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/target/*.jar /app/app.jar
ENV PORT={port}
{_common_footer(port=port)}CMD ["java", "-jar", "/app/app.jar"]
# Image: {app_name}
"""
