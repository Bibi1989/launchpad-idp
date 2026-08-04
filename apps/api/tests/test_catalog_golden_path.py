from __future__ import annotations

from pathlib import Path

from app.services.golden_path_templates import get_golden_path_template, list_golden_path_templates
from app.services.service_scorecard import compute_workspace_scorecard


REQUIRED_TEMPLATE_IDS = {
    "fastapi-api",
    "express-api",
    "nestjs-api",
    "nextjs-web",
    "nuxt-web",
    "fullstack-nuxt-fastapi",
    "fullstack-nextjs-nestjs",
    "fullstack-nuxt-nestjs",
    "fullstack-nextjs-fastapi",
    "fullstack-nextjs-express",
    "fullstack-nextjs-express-postgres",
    "fullstack-nextjs-express-postgres-redis",
    "fullstack-nuxt-express",
    "fullstack-react-fastapi",
    "fullstack-vue-nestjs",
}


def test_list_golden_path_templates() -> None:
    templates = list_golden_path_templates()
    assert len(templates) >= len(REQUIRED_TEMPLATE_IDS)
    ids = {t.id for t in templates}
    missing = REQUIRED_TEMPLATE_IDS - ids
    assert not missing, f"missing golden path templates: {sorted(missing)}"
    tpl = get_golden_path_template("fastapi-api")
    assert tpl.version == "1.0.0"
    assert tpl.includes_dockerfile
    nextjs = get_golden_path_template("nextjs-web")
    assert nextjs.stack == "nextjs"
    fullstack = get_golden_path_template("fullstack-nextjs-nestjs")
    assert fullstack.frameworks == ("nextjs", "nestjs")
    assert fullstack.includes_iac is True
    assert "node:22-alpine" in fullstack.docker_images
    pg_tpl = get_golden_path_template("fullstack-nextjs-express-postgres")
    assert pg_tpl.enable_postgres is True
    assert "postgres:16-alpine" in pg_tpl.docker_images
    redis_tpl = get_golden_path_template("fullstack-nextjs-express-postgres-redis")
    assert redis_tpl.enable_postgres is True
    assert redis_tpl.enable_redis is True
    assert "redis:7-alpine" in redis_tpl.docker_images
    fastapi = get_golden_path_template("fastapi-api")
    assert fastapi.docker_images == ("python:3.12-alpine",)


def test_scorecard_passes_with_hardened_scaffold(tmp_path: Path) -> None:
    (tmp_path / "dockers").mkdir()
    (tmp_path / "dockers" / "Dockerfile.app").write_text(
        "FROM python:3.12-slim\nUSER 10001\nCMD [\"python\"]\n",
        encoding="utf-8",
    )
    workflow = tmp_path / "ci" / "github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "deploy.yml").write_text(
        "jobs:\n  scan:\n    steps:\n      - run: trivy image app\n      - run: semgrep scan\n",
        encoding="utf-8",
    )
    k8s = tmp_path / "infra" / "k8s" / "manifests"
    k8s.mkdir(parents=True)
    (k8s / "deployment.yaml").write_text(
        "resources:\n  requests:\n    cpu: 100m\n    memory: 128Mi\n  limits:\n    cpu: 250m\n    memory: 256Mi\n",
        encoding="utf-8",
    )
    scorecard = compute_workspace_scorecard(tmp_path)
    assert scorecard.score == 100
    assert scorecard.passed


def test_catalog_service_create_name_normalization() -> None:
    from app.schemas.catalog import CatalogServiceCreate
    payload = CatalogServiceCreate(
        name="Fnep",
        template_id="fullstack-nextjs-express-postgres",
        owner="team@example.com",
    )
    assert payload.name == "fnep"

