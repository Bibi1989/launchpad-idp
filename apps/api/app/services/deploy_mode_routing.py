"""Normalize and describe preview deploy modes for workers and services."""

from __future__ import annotations

from app.schemas.k8s import DeployMode

NON_K8S_DEPLOY_MODES = frozenset({
    DeployMode.ATTACH.value,
    DeployMode.COMPOSE.value,
    DeployMode.DOCKER_COMPOSE.value,
    DeployMode.DOCKER_COMPOSE_UNDERSCORE.value,
})


def normalize_deploy_mode(raw: object | None) -> str:
    value = str(raw or DeployMode.PREVIEW.value).strip().lower()
    if value in (DeployMode.DOCKER_COMPOSE.value, DeployMode.DOCKER_COMPOSE_UNDERSCORE.value):
        return DeployMode.COMPOSE.value
    try:
        return DeployMode(value).value
    except ValueError:
        return DeployMode.PREVIEW.value


def init_workflow_message(deploy_mode: str) -> str:
    mode = normalize_deploy_mode(deploy_mode)
    if mode == DeployMode.COMPOSE.value:
        return "INIT - starting Compose deploy workflow"
    if mode == DeployMode.ATTACH.value:
        return "INIT - starting running-instance deploy workflow"
    if mode == DeployMode.MANIFEST.value:
        return "INIT - starting Kubernetes manifest provision workflow"
    return "INIT - starting Kubernetes provision workflow"

