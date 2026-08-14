"""Deploy-time container CVE scan (Trivy) before registry push."""

from __future__ import annotations

import subprocess
from typing import Any

from app.core.logging import get_logger, sanitize_log_message
from app.schemas.cloud import (
    ImageSecurityScanConfig,
    ScanFindingAction,
    ScanSeverityThreshold,
)

logger = get_logger(__name__)

_DEFAULT_TRIVY_IMAGE = "aquasec/trivy:0.58.1"
_TRIVY_IMAGES: dict[str, str] = {
    "trivy-0.58.1": "aquasec/trivy:0.58.1",
    "trivy-0.57.2": "aquasec/trivy:0.57.2",
    "trivy-0.56.2": "aquasec/trivy:0.56.2",
    "trivy-action-v0.30.0": "aquasec/trivy:0.58.1",
}


class ImageSecurityScanError(RuntimeError):
    """Trivy found CVEs at or above the configured gate (block mode)."""


def parse_image_scan_config(raw: Any) -> ImageSecurityScanConfig:
    """Parse wizard / environment JSON into ``ImageSecurityScanConfig``."""
    if raw is None or raw == "":
        return ImageSecurityScanConfig()
    try:
        if isinstance(raw, ImageSecurityScanConfig):
            return raw
        if isinstance(raw, str):
            return ImageSecurityScanConfig.model_validate_json(raw)
        if isinstance(raw, dict):
            return ImageSecurityScanConfig.model_validate(raw)
    except (ValueError, TypeError):
        logger.warning("image_scan_config_invalid")
    return ImageSecurityScanConfig()


def dump_image_scan_config(config: ImageSecurityScanConfig | None) -> str | None:
    if config is None:
        return None
    return config.model_dump_json()


def scan_local_docker_image(*, image: str, config: object | None) -> None:
    """Scan a local Docker image with Trivy. No-op when scanning is disabled."""
    parsed = parse_image_scan_config(config)
    tag = (image or "").strip()
    if not parsed.enabled or not tag:
        return
    trivy_image = _TRIVY_IMAGES.get((parsed.tool or "").strip(), _DEFAULT_TRIVY_IMAGE)
    severity = (
        "CRITICAL"
        if parsed.severity_threshold == ScanSeverityThreshold.CRITICAL
        else "CRITICAL,HIGH"
    )
    fail = parsed.on_finding == ScanFindingAction.BLOCK
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        trivy_image,
        "image",
        "--scanners",
        "vuln",
        "--exit-code",
        "1" if fail else "0",
        "--severity",
        severity,
        "--ignore-unfixed",
        "--no-progress",
        tag,
    ]
    logger.info(
        "image_security_scan_started",
        image=tag,
        severity=severity,
        on_finding=parsed.on_finding.value,
        tool=trivy_image,
    )
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImageSecurityScanError(
            "docker CLI is required to run the container image security scan"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ImageSecurityScanError(
            f"Image security scan timed out for {tag}"
        ) from exc
    detail = sanitize_log_message(
        ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()[-1500:]
    )
    if completed.returncode != 0:
        if fail:
            raise ImageSecurityScanError(
                f"Image security scan blocked {tag} "
                f"(severity {severity}). {detail}"
            )
        logger.warning(
            "image_security_scan_warned",
            image=tag,
            detail=detail[:400],
        )
        return
    logger.info("image_security_scan_passed", image=tag, severity=severity)
