"""Generate Launchpad workspace artifacts from detected repository services."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pkg.detector.models import DetectedService, DetectionResult, ServiceRole


@dataclass
class GeneratedWorkspace:
    files: list[str] = field(default_factory=list)
    dockerfiles: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    preview_service: str | None = None


_STACK_TO_SCAFFOLD: dict[str, str] = {
    "nextjs": "nextjs",
    "nuxtjs": "nuxtjs",
    "vite": "react_vite",
    "vue": "vuejs",
    "svelte": "svelte",
    "angular": "angular",
    "astro": "react_vite",
    "express": "express",
    "nestjs": "nestjs",
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "go": "go",
    "gin": "go",
    "springboot": "springboot",
    "rust": "rust",
    "node": "node",
    "python": "python",
}


class WorkspaceGenerator:
    """Map detected services into Dockerfiles + Kubernetes manifests."""

    def generate(
        self,
        workspace_dir: Path,
        detection: DetectionResult,
        *,
        workspace_name: str,
        services: list[DetectedService] | None = None,
    ) -> GeneratedWorkspace:
        workspace_dir = workspace_dir.resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        active = [s for s in (services or detection.services) if s.enabled]
        if not active:
            raise ValueError("At least one service must be enabled")

        result = GeneratedWorkspace()
        result.preview_service = next(
            (s.name for s in active if s.is_preview_target),
            active[0].name,
        )

        # Dockerfiles for packages missing one
        for svc in active:
            if svc.has_dockerfile:
                continue
            written = self._scaffold_dockerfile(workspace_dir, svc)
            if written:
                result.dockerfiles.append(written)
                result.files.append(written)

        # Datastore + multi-service K8s manifests via Launchpad k8s_bundle helpers
        manifests = self._write_k8s_manifests(
            workspace_dir,
            workspace_name=workspace_name,
            services=active,
            datastores=detection.datastores,
            preview_service=result.preview_service,
        )
        result.manifests.extend(manifests)
        result.files.extend(manifests)

        # Ingress routing: / → web, /api → first API
        ingress = self._write_ingress(
            workspace_dir,
            workspace_name=workspace_name,
            services=active,
            preview_service=result.preview_service,
        )
        if ingress:
            result.manifests.append(ingress)
            result.files.append(ingress)

        meta = workspace_dir / ".launchpad" / "detected-stack.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(
            detection.model_dump_json(indent=2),
            encoding="utf-8",
        )
        result.files.append(".launchpad/detected-stack.json")

        image_builds = self._write_image_build_plan(workspace_dir, active)
        if image_builds:
            result.files.append(".launchpad/image-builds.json")

        readme = workspace_dir / "IMPORT.md"
        readme.write_text(self._readme(workspace_name, active, detection), encoding="utf-8")
        result.files.append("IMPORT.md")
        return result

    def _write_image_build_plan(
        self,
        workspace_dir: Path,
        services: list[DetectedService],
    ) -> list[dict[str, str]]:
        """Record Dockerfile → image tag mappings for the provision-time builder."""
        import json

        plans: list[dict[str, str]] = []
        for svc in services:
            pkg_dir = workspace_dir if svc.path in {".", ""} else (workspace_dir / svc.path)
            candidates: list[Path] = []
            if svc.dockerfile_path:
                rel = svc.dockerfile_path
                candidates.append(pkg_dir / rel)
                candidates.append(workspace_dir / rel)
            candidates.extend([pkg_dir / "Dockerfile", pkg_dir / "dockerfile", workspace_dir / "Dockerfile"])
            dockerfile = next((p.resolve() for p in candidates if p.is_file()), None)
            if dockerfile is None:
                continue
            try:
                df_rel = str(dockerfile.relative_to(workspace_dir.resolve())).replace("\\", "/")
                ctx_path = dockerfile.parent.resolve()
                ctx_rel = (
                    "."
                    if ctx_path == workspace_dir.resolve()
                    else str(ctx_path.relative_to(workspace_dir.resolve())).replace("\\", "/")
                )
            except ValueError:
                continue
            plans.append(
                {
                    "service": svc.name,
                    "image": f"{svc.name}:latest",
                    "context": ctx_rel,
                    "dockerfile": df_rel,
                }
            )
        out = workspace_dir / ".launchpad" / "image-builds.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plans, indent=2), encoding="utf-8")
        return plans

    def _scaffold_dockerfile(self, workspace_dir: Path, svc: DetectedService) -> str | None:
        try:
            from app.schemas.dockerfile_schema import ProjectStack
            from app.services.dockerfile_scaffold import scaffold_dockerfile
        except ImportError:
            return self._fallback_dockerfile(workspace_dir, svc)

        stack_key = _STACK_TO_SCAFFOLD.get(svc.framework, "generic")
        try:
            stack = ProjectStack(stack_key)
        except ValueError:
            stack = ProjectStack.GENERIC

        content = scaffold_dockerfile(stack, app_name=svc.name, listen_port=svc.port)
        pkg_dir = workspace_dir if svc.path in {".", ""} else workspace_dir / svc.path
        pkg_dir.mkdir(parents=True, exist_ok=True)
        target = pkg_dir / "Dockerfile"
        target.write_text(content, encoding="utf-8")
        rel = str(target.relative_to(workspace_dir)).replace("\\", "/")
        return rel

    def _fallback_dockerfile(self, workspace_dir: Path, svc: DetectedService) -> str:
        pkg_dir = workspace_dir if svc.path in {".", ""} else workspace_dir / svc.path
        pkg_dir.mkdir(parents=True, exist_ok=True)
        target = pkg_dir / "Dockerfile"
        target.write_text(
            f"# Generated by Launchpad import for {svc.name} ({svc.framework})\n"
            "FROM nginx:1.27-alpine\n"
            f"EXPOSE {svc.port}\n",
            encoding="utf-8",
        )
        return str(target.relative_to(workspace_dir)).replace("\\", "/")

    def _write_k8s_manifests(
        self,
        workspace_dir: Path,
        *,
        workspace_name: str,
        services: list[DetectedService],
        datastores: list[str],
        preview_service: str,
    ) -> list[str]:
        try:
            from app.schemas.cloud import (
                DataStoreDependency,
                KubernetesPackaging,
                KubernetesWorkloadOptions,
                WorkloadDependenciesConfig,
            )
            from app.services.k8s_bundle import (
                additional_workload_manifests,
                prune_orphan_default_manifests,
                write_kubernetes_layout,
            )
        except ImportError:
            return self._write_minimal_manifests(workspace_dir, workspace_name, services, preview_service)

        deps = WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled="postgres" in datastores),
            redis=DataStoreDependency(enabled="redis" in datastores),
            mongodb=DataStoreDependency(enabled="mongodb" in datastores),
        )
        # Namespace + optional datastores via standard layout (single app placeholder),
        # then replace with per-service launch-* manifests.
        written = write_kubernetes_layout(
            workspace_dir,
            name=workspace_name,
            packaging=KubernetesPackaging.RAW_MANIFESTS,
            options=KubernetesWorkloadOptions(ingress=False, secret=deps.any_enabled()),
            dependencies=deps,
        )
        svc_payload = []
        for svc in services:
            image = f"{svc.name}:latest"
            svc_payload.append(
                {
                    "name": svc.name,
                    "image": image,
                    "port": svc.port,
                    "service_type": "ClusterIP",
                    "selector": svc.name,
                    "health_path": svc.health_path,
                    "expose_preview": svc.name == preview_service,
                    "extra_env": dict(svc.env_hints),
                }
            )
        written.extend(
            additional_workload_manifests(
                workspace_dir,
                env_name=workspace_name,
                services=svc_payload,
                dependencies=deps,
            )
        )
        written.extend(prune_orphan_default_manifests(workspace_dir))
        return written

    def _write_minimal_manifests(
        self,
        workspace_dir: Path,
        workspace_name: str,
        services: list[DetectedService],
        preview_service: str,
    ) -> list[str]:
        ns = re.sub(r"[^a-z0-9-]", "-", workspace_name.lower()).strip("-")[:63] or "lp-import"
        mdir = workspace_dir / "infra" / "k8s" / "manifests"
        mdir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        ns_file = mdir / "namespace.yaml"
        ns_file.write_text(
            f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {ns}\n",
            encoding="utf-8",
        )
        written.append("infra/k8s/manifests/namespace.yaml")
        for svc in services:
            dep = mdir / f"{svc.name}-deployment.yaml"
            svc_file = mdir / f"{svc.name}-service.yaml"
            ann = (
                '  annotations:\n    launchpad.io/preview-target: "true"\n'
                if svc.name == preview_service
                else ""
            )
            dep.write_text(
                f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {svc.name}
  namespace: {ns}
{ann}spec:
  replicas: 1
  selector:
    matchLabels:
      app: {svc.name}
  template:
    metadata:
      labels:
        app: {svc.name}
    spec:
      containers:
        - name: {svc.name}
          image: {svc.name}:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: {svc.port}
""",
                encoding="utf-8",
            )
            svc_file.write_text(
                f"""apiVersion: v1
kind: Service
metadata:
  name: {svc.name}-service
  namespace: {ns}
spec:
  selector:
    app: {svc.name}
  ports:
    - port: {svc.port}
      targetPort: {svc.port}
""",
                encoding="utf-8",
            )
            written.append(f"infra/k8s/manifests/{svc.name}-deployment.yaml")
            written.append(f"infra/k8s/manifests/{svc.name}-service.yaml")
        return written

    def _write_ingress(
        self,
        workspace_dir: Path,
        *,
        workspace_name: str,
        services: list[DetectedService],
        preview_service: str,
    ) -> str | None:
        web = next((s for s in services if s.role == ServiceRole.WEB), None)
        api = next((s for s in services if s.role == ServiceRole.API), None)
        if web is None and api is None:
            return None
        ns = re.sub(r"[^a-z0-9-]", "-", workspace_name.lower()).strip("-")[:63] or "lp-import"
        primary = web or next(s for s in services if s.name == preview_service)
        paths = [
            f"""
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: {primary.name}-service
                  port:
                    number: {primary.port}
"""
        ]
        if api is not None and (web is None or api.name != primary.name):
            paths.append(
                f"""
            - path: /api
              pathType: Prefix
              backend:
                service:
                  name: {api.name}-service
                  port:
                    number: {api.port}
"""
            )
        content = f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {ns}-ingress
  namespace: {ns}
  annotations:
    launchpad.io/generated-by: repo-import
    kubernetes.io/ingress.class: nginx
spec:
  ingressClassName: nginx
  rules:
    - http:
        paths:{"".join(paths)}
"""
        path = workspace_dir / "infra" / "k8s" / "manifests" / "ingress.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return "infra/k8s/manifests/ingress.yaml"

    @staticmethod
    def _readme(
        workspace_name: str,
        services: list[DetectedService],
        detection: DetectionResult,
    ) -> str:
        lines = [
            f"# {workspace_name}",
            "",
            "Generated by Launchpad Repository Import.",
            "",
            f"- Layout: **{detection.layout.value}**",
            f"- Datastores: {', '.join(detection.datastores) or 'none'}",
            "",
            "## Services",
            "",
        ]
        for svc in services:
            preview = " (preview target)" if svc.is_preview_target else ""
            lines.append(
                f"- `{svc.name}` · {svc.role.value}/{svc.framework} · "
                f"port {svc.port} · path `{svc.path}`{preview}"
            )
        lines.extend(
            [
                "",
                "## Next steps",
                "",
                "1. Review manifests under `infra/k8s/manifests/`.",
                "2. Open Launchpad → Launch and select this workspace.",
                "3. Or push to GitHub/GitLab from the workspace page.",
                "",
            ]
        )
        return "\n".join(lines)
