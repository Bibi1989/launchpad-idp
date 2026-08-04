"""Org-approved golden path service templates (versioned stacks)."""

from __future__ import annotations

from dataclasses import dataclass


# Runtime / base images used by Launchpad Dockerfile scaffolds (per framework).
_FRAMEWORK_DOCKER_IMAGES: dict[str, tuple[str, ...]] = {
    "fastapi": ("python:3.12-alpine",),
    "flask": ("python:3.12-alpine",),
    "django": ("python:3.12-alpine",),
    "python": ("python:3.12-alpine",),
    "express": ("node:22-alpine",),
    "nestjs": ("node:22-alpine",),
    "node": ("node:22-alpine",),
    "nextjs": ("node:22-alpine",),
    "nuxtjs": ("node:22-alpine",),
    "react_vite": ("node:22-alpine", "nginxinc/nginx-unprivileged:alpine"),
    "vuejs": ("node:22-alpine", "caddy:2.8-alpine"),
    "svelte": ("node:22-alpine", "busybox:1.36-uclibc"),
    "go": ("golang:1.23-alpine", "alpine:3.21"),
    "rust": ("rust:1.83-alpine", "alpine:3.21"),
    "springboot": ("eclipse-temurin:21-jdk-alpine", "eclipse-temurin:21-jre-alpine"),
    "java": ("eclipse-temurin:21-jdk-alpine", "eclipse-temurin:21-jre-alpine"),
    "generic": ("alpine:3.21",),
}


def docker_images_for_frameworks(frameworks: tuple[str, ...] | list[str]) -> list[str]:
    """Deduped Docker images used by scaffold Dockerfiles for the given stacks."""
    images: list[str] = []
    seen: set[str] = set()
    for framework in frameworks:
        for image in _FRAMEWORK_DOCKER_IMAGES.get(framework, ()):
            if image not in seen:
                seen.add(image)
                images.append(image)
    return images


@dataclass(frozen=True, slots=True)
class GoldenPathTemplate:
    id: str
    version: str
    title: str
    description: str
    icon: str
    stack: str
    frameworks: tuple[str, ...]
    default_tier: str
    default_slo: str
    listen_port: int
    tags: tuple[str, ...]
    includes_dockerfile: bool = True
    includes_k8s: bool = True
    includes_cicd: bool = True
    includes_iac: bool = False
    enable_postgres: bool = False
    enable_redis: bool = False

    @property
    def docker_images(self) -> tuple[str, ...]:
        images = list(docker_images_for_frameworks(self.frameworks))
        if self.enable_postgres and "postgres:16-alpine" not in images:
            images.append("postgres:16-alpine")
        if self.enable_redis and "redis:7-alpine" not in images:
            images.append("redis:7-alpine")
        return tuple(images)


GOLDEN_PATH_TEMPLATES: tuple[GoldenPathTemplate, ...] = (
    # --- Single services ---
    GoldenPathTemplate(
        id="fastapi-api",
        version="1.0.0",
        title="FastAPI service",
        description="Python FastAPI API with Dockerfile, raw K8s manifests, and GitHub Actions (Trivy + Semgrep).",
        icon="api",
        stack="fastapi",
        frameworks=("fastapi",),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=8000,
        tags=("python", "api", "backend"),
    ),
    GoldenPathTemplate(
        id="express-api",
        version="1.0.0",
        title="Express API",
        description="Node Express API with hardened multi-stage Dockerfile, K8s Deployment/Service, and CI security scans.",
        icon="terminal",
        stack="express",
        frameworks=("express",),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=3000,
        tags=("node", "api", "backend"),
    ),
    GoldenPathTemplate(
        id="nestjs-api",
        version="1.0.0",
        title="NestJS API",
        description="NestJS TypeScript API with multi-stage Dockerfile, K8s packaging, and pinned CI security stages.",
        icon="data_object",
        stack="nestjs",
        frameworks=("nestjs",),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=3000,
        tags=("node", "typescript", "api", "backend"),
    ),
    GoldenPathTemplate(
        id="flask-api",
        version="1.0.0",
        title="Flask API",
        description="Python Flask service with slim Dockerfile, K8s manifests, and Trivy/SAST CI.",
        icon="science",
        stack="flask",
        frameworks=("flask",),
        default_tier="tier-3",
        default_slo="99.0",
        listen_port=5000,
        tags=("python", "api", "backend"),
    ),
    GoldenPathTemplate(
        id="django-api",
        version="1.0.0",
        title="Django service",
        description="Django app golden path with container scaffold, Kubernetes resources, and GitHub Actions security.",
        icon="dynamic_form",
        stack="django",
        frameworks=("django",),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=8000,
        tags=("python", "api", "backend"),
    ),
    GoldenPathTemplate(
        id="go-api",
        version="1.0.0",
        title="Go API",
        description="Go HTTP service with static binary Dockerfile, K8s Deployment/Service, and CI scanning.",
        icon="memory",
        stack="go",
        frameworks=("go",),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=8080,
        tags=("go", "api", "backend"),
    ),
    GoldenPathTemplate(
        id="springboot-api",
        version="1.0.0",
        title="Spring Boot API",
        description="Java Spring Boot service with multi-stage JVM image, K8s packaging, and CI security gates.",
        icon="coffee",
        stack="springboot",
        frameworks=("springboot",),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=8080,
        tags=("java", "api", "backend"),
    ),
    # --- Frontends ---
    GoldenPathTemplate(
        id="nextjs-web",
        version="1.0.0",
        title="Next.js web app",
        description="Next.js frontend with standalone/output Docker image, K8s Ingress-ready manifests, and CI/CD.",
        icon="web_asset",
        stack="nextjs",
        frameworks=("nextjs",),
        default_tier="tier-3",
        default_slo="99.0",
        listen_port=3000,
        tags=("frontend", "nextjs", "react", "web"),
    ),
    GoldenPathTemplate(
        id="nuxt-web",
        version="1.0.0",
        title="Nuxt web app",
        description="Nuxt frontend golden path with container scaffold, K8s Ingress-ready manifests, and GitHub workflow.",
        icon="web",
        stack="nuxtjs",
        frameworks=("nuxtjs",),
        default_tier="tier-3",
        default_slo="99.0",
        listen_port=3000,
        tags=("frontend", "nuxt", "vue", "web"),
    ),
    GoldenPathTemplate(
        id="react-vite-web",
        version="1.0.0",
        title="React (Vite) web app",
        description="React + Vite SPA with nginx static image, K8s Service, and CI security scanning.",
        icon="javascript",
        stack="react_vite",
        frameworks=("react_vite",),
        default_tier="tier-3",
        default_slo="99.0",
        listen_port=80,
        tags=("frontend", "react", "vite", "web"),
    ),
    GoldenPathTemplate(
        id="vue-web",
        version="1.0.0",
        title="Vue web app",
        description="Vue SPA golden path with static nginx Dockerfile, K8s packaging, and CI/CD.",
        icon="view_quilt",
        stack="vuejs",
        frameworks=("vuejs",),
        default_tier="tier-3",
        default_slo="99.0",
        listen_port=80,
        tags=("frontend", "vue", "web"),
    ),
    # --- Fullstack ---
    GoldenPathTemplate(
        id="fullstack-nuxt-fastapi",
        version="1.1.0",
        title="Fullstack (Nuxt + FastAPI)",
        description="Approved dual-stack: Nuxt UI + FastAPI API, shared docker-compose, K8s packaging, and CI/CD.",
        icon="dashboard",
        stack="nuxtjs",
        frameworks=("nuxtjs", "fastapi"),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=3000,
        tags=("fullstack", "python", "nuxt", "node"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-nextjs-nestjs",
        version="1.0.0",
        title="Fullstack (Next.js + NestJS)",
        description="Next.js UI + NestJS API with dual Dockerfiles, compose, Kubernetes, and security-gated CI.",
        icon="hub",
        stack="nextjs",
        frameworks=("nextjs", "nestjs"),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=3000,
        tags=("fullstack", "nextjs", "nestjs", "typescript"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-nuxt-nestjs",
        version="1.0.0",
        title="Fullstack (Nuxt + NestJS)",
        description="Nuxt frontend + NestJS backend golden path with multi-service compose and K8s manifests.",
        icon="account_tree",
        stack="nuxtjs",
        frameworks=("nuxtjs", "nestjs"),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=3000,
        tags=("fullstack", "nuxt", "nestjs", "typescript"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-nextjs-fastapi",
        version="1.0.0",
        title="Fullstack (Next.js + FastAPI)",
        description="Next.js UI + FastAPI API — dual containers, K8s packaging, Terraform optional, CI security scans.",
        icon="join_inner",
        stack="nextjs",
        frameworks=("nextjs", "fastapi"),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=3000,
        tags=("fullstack", "nextjs", "python", "fastapi"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-nextjs-express",
        version="1.0.0",
        title="Fullstack (Next.js + Express)",
        description="Next.js UI + Express API with dual Dockerfiles, docker-compose, K8s, and GitHub Actions.",
        icon="device_hub",
        stack="nextjs",
        frameworks=("nextjs", "express"),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=3000,
        tags=("fullstack", "nextjs", "express", "node"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-nextjs-express-postgres",
        version="1.0.0",
        title="Fullstack (Next.js + Express + PostgreSQL)",
        description="Next.js UI + Express API + PostgreSQL database — dual containers, database provisioning, K8s packaging, and security CI.",
        icon="storage",
        stack="nextjs",
        frameworks=("nextjs", "express"),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=3000,
        tags=("fullstack", "nextjs", "express", "postgres", "node", "database"),
        includes_iac=True,
        enable_postgres=True,
        enable_redis=False,
    ),
    GoldenPathTemplate(
        id="fullstack-nextjs-express-postgres-redis",
        version="1.0.0",
        title="Fullstack (Next.js + Express + PostgreSQL + Redis)",
        description="Next.js UI + Express API + PostgreSQL + Redis cache — full-stack production architecture with database and cache provisioning.",
        icon="layers",
        stack="nextjs",
        frameworks=("nextjs", "express"),
        default_tier="tier-1",
        default_slo="99.9",
        listen_port=3000,
        tags=("fullstack", "nextjs", "express", "postgres", "redis", "node", "cache"),
        includes_iac=True,
        enable_postgres=True,
        enable_redis=True,
    ),
    GoldenPathTemplate(
        id="fullstack-nuxt-express",
        version="1.0.0",
        title="Fullstack (Nuxt + Express)",
        description="Nuxt UI + Express API golden path with compose, Kubernetes manifests, and CI scanning.",
        icon="lan",
        stack="nuxtjs",
        frameworks=("nuxtjs", "express"),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=3000,
        tags=("fullstack", "nuxt", "express", "node"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-react-fastapi",
        version="1.0.0",
        title="Fullstack (React + FastAPI)",
        description="React/Vite SPA + FastAPI API with nginx + API containers, K8s, and security CI.",
        icon="widgets",
        stack="react_vite",
        frameworks=("react_vite", "fastapi"),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=80,
        tags=("fullstack", "react", "python", "fastapi"),
        includes_iac=True,
    ),
    GoldenPathTemplate(
        id="fullstack-vue-nestjs",
        version="1.0.0",
        title="Fullstack (Vue + NestJS)",
        description="Vue SPA + NestJS API with dual Dockerfiles, K8s packaging, and CI/CD security gates.",
        icon="grid_view",
        stack="vuejs",
        frameworks=("vuejs", "nestjs"),
        default_tier="tier-2",
        default_slo="99.5",
        listen_port=80,
        tags=("fullstack", "vue", "nestjs", "typescript"),
        includes_iac=True,
    ),
)

_BY_ID = {item.id: item for item in GOLDEN_PATH_TEMPLATES}


def list_golden_path_templates() -> list[GoldenPathTemplate]:
    return list(GOLDEN_PATH_TEMPLATES)


def get_golden_path_template(template_id: str) -> GoldenPathTemplate:
    try:
        return _BY_ID[template_id]
    except KeyError as exc:
        raise KeyError(f"Unknown golden path template: {template_id}") from exc
