"""Catalog of one-click preview app templates.

Templates deploy Launchpad's configured workload image (see each template's
``workload_image``) into an ephemeral namespace. Git URLs are real public repos
used for GitOps labels, rebuild matching, and PR association - not a full CI
build pipeline yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreviewAppTemplate:
    id: str
    title: str
    description: str
    icon: str
    git_repo_url: str
    git_branch: str
    default_ttl_hours: int
    hourly_cost_hint: str
    workload_image: str
    tags: tuple[str, ...]
    enable_postgres: bool = False
    enable_redis: bool = False


PREVIEW_TEMPLATES: tuple[PreviewAppTemplate, ...] = (
    PreviewAppTemplate(
        id="hello-web",
        title="Hello Web",
        description=(
            "Nginx welcome page - fastest way to prove Open app + NodePort end-to-end. "
            "Uses the public nginxinc/NGINX-Demos repo for GitOps labels."
        ),
        icon="language",
        git_repo_url="https://github.com/nginxinc/NGINX-Demos.git",
        git_branch="master",
        default_ttl_hours=8,
        hourly_cost_hint="0.12",
        workload_image="nginx:1.27-alpine",
        tags=("beginner", "static", "demo"),
    ),
    PreviewAppTemplate(
        id="node-api",
        title="HTTP Echo API",
        description=(
            "hashicorp/http-echo service on port 80 - lightweight API smoke test for "
            "feature-branch previews."
        ),
        icon="api",
        git_repo_url="https://github.com/hashicorp/http-echo.git",
        git_branch="master",
        default_ttl_hours=12,
        hourly_cost_hint="0.28",
        workload_image="hashicorp/http-echo:1.0",
        tags=("api", "backend", "demo"),
    ),
    PreviewAppTemplate(
        id="fullstack-nextjs-express-postgres",
        title="Fullstack (Next.js + Express + PostgreSQL)",
        description="Next.js UI + Express API + PostgreSQL database - dual containers, database provisioning, K8s packaging.",
        icon="storage",
        git_repo_url="https://github.com/kubernetes/examples.git",
        git_branch="master",
        default_ttl_hours=24,
        hourly_cost_hint="0.45",
        workload_image="nginx:1.27-alpine",
        tags=("fullstack", "nextjs", "express", "postgres", "node", "database"),
        enable_postgres=True,
        enable_redis=False,
    ),
    PreviewAppTemplate(
        id="fullstack-nextjs-express-postgres-redis",
        title="Fullstack (Next.js + Express + PostgreSQL + Redis)",
        description="Next.js UI + Express API + PostgreSQL + Redis cache - full-stack architecture with database and cache provisioning.",
        icon="layers",
        git_repo_url="https://github.com/kubernetes/examples.git",
        git_branch="master",
        default_ttl_hours=24,
        hourly_cost_hint="0.55",
        workload_image="nginx:1.27-alpine",
        tags=("fullstack", "nextjs", "express", "postgres", "redis", "node", "cache"),
        enable_postgres=True,
        enable_redis=True,
    ),
    PreviewAppTemplate(
        id="fullstack-demo",
        title="Fullstack Demo",
        description=(
            "Demo nginx front for stakeholder reviews. Tracks the public "
            "kubernetes/examples repo; swap to your repo for real PR previews."
        ),
        icon="dashboard",
        git_repo_url="https://github.com/kubernetes/examples.git",
        git_branch="master",
        default_ttl_hours=24,
        hourly_cost_hint="0.42",
        workload_image="web-ui:latest",
        tags=("fullstack", "demo", "review"),
    ),
)

_BY_ID = {item.id: item for item in PREVIEW_TEMPLATES}


def list_preview_templates() -> list[PreviewAppTemplate]:
    return list(PREVIEW_TEMPLATES)


def get_preview_template(template_id: str) -> PreviewAppTemplate:
    try:
        return _BY_ID[template_id]
    except KeyError as exc:
        raise KeyError(f"Unknown preview template: {template_id}") from exc
