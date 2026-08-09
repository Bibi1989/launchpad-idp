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
        ProjectStack.NUXTJS: _nuxt_dockerfile,
        ProjectStack.VUEJS: _vue_dockerfile,
        ProjectStack.SVELTE: _svelte_dockerfile,
        ProjectStack.ANGULAR: _angular_dockerfile,
        ProjectStack.DOTNET: _dotnet_dockerfile,
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


def dockerfile_path_for_service(
    app_name: str,
    stack: ProjectStack | None = None,
    *,
    multi: bool = False,
) -> str:
    """Return a meaningful Dockerfile path under ``dockers/``."""
    app_slug = _sanitize_name(app_name)
    if multi and stack is not None:
        return f"dockers/Dockerfile.{app_slug}-{stack.value}"
    return f"dockers/Dockerfile.{app_slug}"


def default_dockerfile_path(app_name: str = "app") -> str:
    return dockerfile_path_for_service(app_name)


def scaffold_docker_compose(
    app_name: str = "app",
    listen_port: int = 8080,
    dockerfile_path: str = "dockers/Dockerfile",
) -> str:
    """Return a production-ready docker-compose.yml file for the application."""
    return scaffold_docker_compose_services(
        [
            {
                "name": app_name,
                "listen_port": listen_port,
                "dockerfile_path": dockerfile_path,
            }
        ]
    )


def _service_is_frontend(service: dict[str, object]) -> bool:
    kind = str(service.get("app_kind") or "").strip().lower()
    if kind == "frontend":
        return True
    if kind == "backend":
        return False
    name = str(service.get("name") or "").strip().lower()
    return any(token in name for token in ("web", "ui", "frontend", "spa", "next", "nuxt"))


def _service_expose_preview(service: dict[str, object]) -> bool:
    explicit = service.get("expose_preview")
    if explicit is not None:
        return bool(explicit)
    return _service_is_frontend(service)


def scaffold_docker_compose_services(
    services: list[dict[str, object]],
    dependency_blocks: list[str] | None = None,
) -> str:
    """Return a docker-compose.yml covering one or more scaffolded services.

    Frontend services are the default Open-app / browser targets
    (``x-launchpad.preview_target`` + host publish). Backends stay on the
    compose network unless ``expose_preview`` is true. Optional ``extra_env``
    and ``depends_on`` wire frontend→backend and backend→datastore links.
    """
    if not services:
        return scaffold_docker_compose()

    # Ensure at least one preview target when multiple services are present.
    normalized = [dict(s) for s in services]
    if normalized and not any(_service_expose_preview(s) for s in normalized):
        frontends = [s for s in normalized if _service_is_frontend(s)]
        (frontends[0] if frontends else normalized[0])["expose_preview"] = True

    lines: list[str] = ["services:"]
    for service in normalized:
        raw_name = str(service.get("name") or "app")
        safe_name = _sanitize_name(raw_name)
        port = int(service.get("listen_port") or 8080)
        dockerfile_path = str(service.get("dockerfile_path") or "dockers/Dockerfile")
        context = str(service.get("context") or ".")
        health_path = str(service.get("health_path") or "/health")
        image_env = f"APP_IMAGE_{safe_name.upper().replace('-', '_').replace('.', '_')}"
        is_frontend = _service_is_frontend(service)
        app_kind = "frontend" if is_frontend else str(service.get("app_kind") or "backend")
        expose_preview = _service_expose_preview(service)
        extra_env = service.get("extra_env") if isinstance(service.get("extra_env"), dict) else {}
        depends_on = service.get("depends_on") if isinstance(service.get("depends_on"), list) else []

        lines.extend(
            [
                f"  {safe_name}:",
                "    build:",
                f"      context: {context}",
                f"      dockerfile: {dockerfile_path}",
                f"    image: ${{{image_env}:-{safe_name}:latest}}",
                "    labels:",
                f"      - launchpad.io/app-kind={app_kind}",
                f"      - launchpad.io/preview-target={'true' if expose_preview else 'false'}",
                "    x-launchpad:",
                f"      app_kind: {app_kind}",
                f"      preview_target: {'true' if expose_preview else 'false'}",
            ]
        )
        if expose_preview:
            lines.extend(
                [
                    "    ports:",
                    # Preferred host port; compose deploy remaps if busy.
                    f'      - "{port}:{port}"',
                ]
            )
        else:
            lines.extend(
                [
                    "    expose:",
                    f'      - "{port}"',
                ]
            )
        lines.extend(
            [
                "    environment:",
                f"      - PORT={port}",
                "      - NODE_ENV=production",
                "      - LAUNCHPAD_RUNTIME=compose",
            ]
        )
        for key, value in extra_env.items():
            env_key = str(key).strip()
            if not env_key:
                continue
            lines.append(f"      - {env_key}={value}")
        if depends_on:
            lines.append("    depends_on:")
            for dep in depends_on:
                dep_name = _sanitize_name(str(dep))
                if dep_name:
                    lines.append(f"      - {dep_name}")
        lines.extend(
            [
                "    restart: unless-stopped",
                "    healthcheck:",
                f'      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:{port}{health_path}"]',
                "      interval: 30s",
                "      timeout: 5s",
                "      retries: 3",
                "      start_period: 20s",
                "",
            ]
        )
    if dependency_blocks:
        lines.extend(dependency_blocks)
    return "\n".join(lines).rstrip() + "\n"


def scaffold_dependency_compose_blocks(
    dependencies: object,
) -> list[str]:
    """Return docker-compose service blocks for enabled in-cluster datastores."""
    from app.schemas.cloud import DependencyPlacement, WorkloadDependenciesConfig

    if not isinstance(dependencies, WorkloadDependenciesConfig):
        return []
    lines: list[str] = []
    if (
        dependencies.postgres.enabled
        and dependencies.postgres.placement == DependencyPlacement.IN_CLUSTER
    ):
        lines.extend(
            [
                "  postgres:",
                "    image: postgres:16-alpine",
                "    environment:",
                "      POSTGRES_USER: launchpad",
                "      POSTGRES_PASSWORD: changeme",
                "      POSTGRES_DB: app",
                # Internal-only: backends connect via service DNS, not host publish.
                "    expose:",
                '      - "5432"',
                "",
            ]
        )
    if (
        dependencies.mysql.enabled
        and dependencies.mysql.placement == DependencyPlacement.IN_CLUSTER
    ):
        lines.extend(
            [
                "  mysql:",
                "    image: mysql:8.4",
                "    environment:",
                "      MYSQL_ROOT_PASSWORD: changeme",
                "      MYSQL_DATABASE: app",
                "      MYSQL_USER: launchpad",
                "      MYSQL_PASSWORD: changeme",
                "    expose:",
                '      - "3306"',
                "",
            ]
        )
    if (
        dependencies.mariadb.enabled
        and dependencies.mariadb.placement == DependencyPlacement.IN_CLUSTER
    ):
        lines.extend(
            [
                "  mariadb:",
                "    image: mariadb:11",
                "    environment:",
                "      MARIADB_ROOT_PASSWORD: changeme",
                "      MARIADB_DATABASE: app",
                "      MARIADB_USER: launchpad",
                "      MARIADB_PASSWORD: changeme",
                "    expose:",
                '      - "3306"',
                "",
            ]
        )
    if (
        dependencies.mongodb.enabled
        and dependencies.mongodb.placement == DependencyPlacement.IN_CLUSTER
    ):
        lines.extend(
            [
                "  mongodb:",
                "    image: mongo:7",
                "    environment:",
                "      MONGO_INITDB_ROOT_USERNAME: launchpad",
                "      MONGO_INITDB_ROOT_PASSWORD: changeme",
                "    expose:",
                '      - "27017"',
                "",
            ]
        )
    if (
        dependencies.redis.enabled
        and dependencies.redis.placement == DependencyPlacement.IN_CLUSTER
    ):
        lines.extend(
            [
                "  redis:",
                "    image: redis:7-alpine",
                "    expose:",
                '      - "6379"',
                "",
            ]
        )
    return lines


def compose_dependency_env_and_depends(
    dependencies: object,
) -> tuple[dict[str, str], list[str]]:
    """Env vars + depends_on names for backends talking to compose datastores."""
    from app.schemas.cloud import DependencyPlacement, WorkloadDependenciesConfig

    if not isinstance(dependencies, WorkloadDependenciesConfig):
        return {}, []
    env: dict[str, str] = {}
    depends: list[str] = []
    if (
        dependencies.postgres.enabled
        and dependencies.postgres.placement == DependencyPlacement.IN_CLUSTER
    ):
        env["DATABASE_URL"] = "postgresql://launchpad:changeme@postgres:5432/app"
        depends.append("postgres")
    elif (
        dependencies.mysql.enabled
        and dependencies.mysql.placement == DependencyPlacement.IN_CLUSTER
    ):
        env["DATABASE_URL"] = "mysql://launchpad:changeme@mysql:3306/app"
        env["MYSQL_URL"] = env["DATABASE_URL"]
        depends.append("mysql")
    elif (
        dependencies.mariadb.enabled
        and dependencies.mariadb.placement == DependencyPlacement.IN_CLUSTER
    ):
        env["DATABASE_URL"] = "mysql://launchpad:changeme@mariadb:3306/app"
        env["MYSQL_URL"] = env["DATABASE_URL"]
        depends.append("mariadb")
    elif (
        dependencies.mongodb.enabled
        and dependencies.mongodb.placement == DependencyPlacement.IN_CLUSTER
    ):
        env["DATABASE_URL"] = "mongodb://launchpad:changeme@mongodb:27017"
        env["MONGODB_URI"] = env["DATABASE_URL"]
        depends.append("mongodb")
    if (
        dependencies.redis.enabled
        and dependencies.redis.placement == DependencyPlacement.IN_CLUSTER
    ):
        env["REDIS_URL"] = "redis://redis:6379/0"
        depends.append("redis")
    return env, depends


def wire_compose_service_links(
    services: list[dict[str, object]],
    *,
    dependencies: object | None = None,
) -> list[dict[str, object]]:
    """Attach frontend→backend and backend→datastore links for compose."""
    if not services:
        return services
    wired = [dict(s) for s in services]
    backends = [s for s in wired if not _service_is_frontend(s)]
    frontends = [s for s in wired if _service_is_frontend(s)]
    primary_backend = backends[0] if backends else None
    dep_env, dep_depends = compose_dependency_env_and_depends(dependencies)

    if primary_backend is not None:
        be_name = _sanitize_name(str(primary_backend.get("name") or "api"))
        be_port = int(primary_backend.get("listen_port") or 8080)
        api_url = f"http://{be_name}:{be_port}"
        for fe in frontends:
            extra = dict(fe.get("extra_env") or {})
            extra.update(
                {
                    "API_URL": api_url,
                    "BACKEND_URL": api_url,
                    "NEXT_PUBLIC_API_URL": api_url,
                    "NUXT_PUBLIC_API_URL": api_url,
                }
            )
            fe["extra_env"] = extra
            deps = list(fe.get("depends_on") or [])
            if be_name not in deps:
                deps.append(be_name)
            fe["depends_on"] = deps

    for be in backends:
        extra = dict(be.get("extra_env") or {})
        extra.update(dep_env)
        be["extra_env"] = extra
        deps = list(be.get("depends_on") or [])
        for dep in dep_depends:
            if dep not in deps:
                deps.append(dep)
        be["depends_on"] = deps

    return wired


_DEFAULT_LISTEN_PORTS: Final[dict[ProjectStack, int]] = {
    ProjectStack.REACT_VITE: 80,
    ProjectStack.NEXTJS: 3000,
    ProjectStack.NUXTJS: 3000,
    ProjectStack.VUEJS: 80,
    ProjectStack.SVELTE: 3000,
    ProjectStack.ANGULAR: 80,
    ProjectStack.DOTNET: 8080,
    ProjectStack.FASTAPI: 8000,
    ProjectStack.FLASK: 5000,
    ProjectStack.DJANGO: 8000,
    ProjectStack.EXPRESS: 3000,
    ProjectStack.NESTJS: 3000,
    ProjectStack.SPRINGBOOT: 8080,
    ProjectStack.NODE: 3000,
    ProjectStack.PYTHON: 8000,
    ProjectStack.GO: 8080,
    ProjectStack.JAVA: 8080,
    ProjectStack.RUST: 8080,
    ProjectStack.GENERIC: 8080,
    ProjectStack.UNKNOWN: 8080,
}


def default_listen_port_for_stack(stack: ProjectStack, fallback: int = 8080) -> int:
    return _DEFAULT_LISTEN_PORTS.get(stack, fallback)


def resolve_scaffold_stacks(
    *,
    stack: str,
    frameworks: list[str] | None,
) -> list[ProjectStack]:
    """Prefer explicit multi-select frameworks; fall back to single stack."""
    resolved: list[ProjectStack] = []
    candidates = frameworks if frameworks else [stack]
    for raw in candidates:
        value = str(raw or "").strip().lower()
        if not value:
            continue
        try:
            item = ProjectStack(value)
        except ValueError:
            continue
        if item not in resolved:
            resolved.append(item)
    return resolved or [ProjectStack.NODE]


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


def _vue_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Vue SPA image (Caddy static server, non-root).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM caddy:2.8-alpine AS runtime
COPY --from=build /src/dist /usr/share/caddy
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:80/ || exit 1
CMD ["caddy", "file-server", "--root", "/usr/share/caddy", "--listen", ":80"]
# Image: {app_name}
"""


def _svelte_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Svelte SPA image (BusyBox httpd, non-root).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM busybox:1.36-uclibc AS runtime
COPY --from=build /src/dist /www
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/ || exit 1
CMD ["httpd", "-f", "-p", "8080", "-h", "/www"]
# Image: {app_name}
"""


def _angular_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Angular SPA image (non-root Nginx).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=build --chown=101:101 /src/dist/*/browser /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/ || exit 1
# Image: {app_name}
"""


def _dotnet_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage .NET image (non-root).

FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY *.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app ./
ENV ASPNETCORE_URLS=http://0.0.0.0:{port} PORT={port}
USER 1654
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:{port}/health || exit 1
CMD ["dotnet", "app.dll"]
# Image: {app_name}
"""


def _nuxt_dockerfile(app_name: str, port: int) -> str:
    return f"""\
# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Nuxt SSR image (Node runtime, USER 10001).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/.output ./.output
ENV NODE_ENV=production HOST=0.0.0.0 PORT={port} NITRO_PORT={port} NITRO_HOST=0.0.0.0
{_common_footer(port=port)}CMD ["node", ".output/server/index.mjs"]
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
