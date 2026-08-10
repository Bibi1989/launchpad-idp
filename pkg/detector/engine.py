"""Smart project / monorepo detector for Launchpad repository imports."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pkg.detector.models import (
    DetectedService,
    DetectionResult,
    MonorepoTool,
    ProjectLayout,
    ServiceRole,
)

_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".next",
        "dist",
        "build",
        "target",
        ".turbo",
        ".nx",
        "vendor",
        "__pycache__",
        ".venv",
        "venv",
        "coverage",
    }
)

_WEB_DEPS: dict[str, tuple[str, int, str]] = {
    "next": ("nextjs", 3000, "/"),
    "nuxt": ("nuxtjs", 3000, "/"),
    "vite": ("vite", 5173, "/"),
    "react-scripts": ("create-react-app", 3000, "/"),
    "@angular/core": ("angular", 4200, "/"),
    "svelte": ("svelte", 5173, "/"),
    "astro": ("astro", 4321, "/"),
    "vue": ("vue", 5173, "/"),
}

_API_DEPS: dict[str, tuple[str, int, str]] = {
    "@nestjs/core": ("nestjs", 3000, "/health"),
    "express": ("express", 3000, "/health"),
    "fastify": ("fastify", 3000, "/health"),
    "koa": ("koa", 3000, "/health"),
    "hono": ("hono", 3000, "/health"),
}

_PORT_BY_FRAMEWORK: dict[str, int] = {
    "nextjs": 3000,
    "nuxtjs": 3000,
    "vite": 5173,
    "vue": 5173,
    "svelte": 5173,
    "angular": 4200,
    "astro": 4321,
    "express": 3000,
    "nestjs": 3000,
    "fastapi": 8000,
    "flask": 5000,
    "django": 8000,
    "go": 8080,
    "gin": 8080,
    "springboot": 8080,
    "rust": 8080,
}


class ProjectDetectorEngine:
    """Scan a cloned repository tree and classify apps/services."""

    def detect(self, root: Path) -> DetectionResult:
        root = root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Repository root not found: {root}")

        root_names = {p.name for p in root.iterdir() if p.is_file()}
        tools = self._detect_monorepo_tools(root, root_names)
        package_globs = self._resolve_package_globs(root, root_names, tools)
        package_dirs = self._discover_packages(root, package_globs, tools)
        is_monorepo = bool(tools - {MonorepoTool.NONE, MonorepoTool.MAKE}) or len(package_dirs) > 1

        services: list[DetectedService] = []
        if is_monorepo and package_dirs:
            for pkg_dir in package_dirs:
                svc = self._classify_package(root, pkg_dir)
                if svc is not None:
                    services.append(svc)
        else:
            svc = self._classify_package(root, root)
            if svc is not None:
                services.append(svc)

        if not services:
            services.append(
                DetectedService(
                    id="root",
                    name="launch-app",
                    path=".",
                    role=ServiceRole.UNKNOWN,
                    framework="generic",
                    runtime="unknown",
                    port=8080,
                    has_dockerfile=(root / "Dockerfile").is_file(),
                    dockerfile_path="Dockerfile" if (root / "Dockerfile").is_file() else None,
                    markers=sorted(root_names & {
                        "package.json", "requirements.txt", "pyproject.toml",
                        "go.mod", "Cargo.toml", "Dockerfile", "pom.xml",
                    }),
                )
            )

        services = self._assign_names_and_preview(services)
        datastores = self._detect_datastores(root)
        from pkg.detector.env_example import collect_env_example_vars

        env_example = collect_env_example_vars(root)
        layout = ProjectLayout.MONOREPO if is_monorepo else ProjectLayout.SINGLE
        markers = sorted(
            n
            for n in root_names
            if n
            in {
                "package.json",
                "pnpm-workspace.yaml",
                "lerna.json",
                "turbo.json",
                "nx.json",
                "Cargo.toml",
                "go.work",
                "Makefile",
                "makefile",
                "Dockerfile",
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
                "go.mod",
                "pyproject.toml",
                "requirements.txt",
                ".env.example",
                ".env.sample",
                ".env.template",
            }
        )
        has_compose, has_kubernetes = self._detect_runtime_artifacts(root, root_names)
        return DetectionResult(
            layout=layout,
            monorepo_tools=sorted(tools, key=lambda t: t.value) if tools else [MonorepoTool.NONE],
            services=services,
            datastores=datastores,
            root_markers=markers,
            package_globs=package_globs,
            has_compose=has_compose,
            has_kubernetes=has_kubernetes,
            env_example=env_example,
        )

    def _detect_runtime_artifacts(self, root: Path, root_names: set[str]) -> tuple[bool, bool]:
        """Return (has_compose, has_kubernetes) hints for import mode selection."""
        has_compose = bool(
            root_names
            & {
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            }
        )
        has_kubernetes = False
        hint_dirs = {
            "k8s",
            "kubernetes",
            "deploy",
            "deployment",
            "deployments",
            "manifests",
            "charts",
            "helm",
            "infra",
            ".kubernetes",
        }
        kind_re = re.compile(
            r"(?m)^kind:\s*(Deployment|StatefulSet|DaemonSet|Service|Ingress|CronJob)\s*$"
        )
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            name = path.name.lower()
            if name in {"chart.yaml", "kustomization.yaml", "kustomization.yml"}:
                has_kubernetes = True
                break
            if path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            # Prefer known infra dirs to keep scans cheap on huge repos.
            if not any(part.lower() in hint_dirs for part in path.parts):
                continue
            try:
                sample = path.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            if kind_re.search(sample):
                has_kubernetes = True
                break
        return has_compose, has_kubernetes

    def _detect_monorepo_tools(self, root: Path, root_names: set[str]) -> set[MonorepoTool]:
        tools: set[MonorepoTool] = set()
        if "pnpm-workspace.yaml" in root_names:
            tools.add(MonorepoTool.PNPM)
        if "lerna.json" in root_names:
            tools.add(MonorepoTool.LERNA)
        if "turbo.json" in root_names:
            tools.add(MonorepoTool.TURBO)
        if "nx.json" in root_names:
            tools.add(MonorepoTool.NX)
        if "go.work" in root_names:
            tools.add(MonorepoTool.GO_WORK)
        if "Makefile" in root_names or "makefile" in root_names:
            tools.add(MonorepoTool.MAKE)
        pkg = root / "package.json"
        if pkg.is_file():
            data = self._read_json(pkg)
            workspaces = data.get("workspaces")
            if isinstance(workspaces, (list, dict)):
                tools.add(MonorepoTool.NPM_WORKSPACES)
        cargo = root / "Cargo.toml"
        if cargo.is_file():
            text = cargo.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?m)^\[workspace\]", text):
                tools.add(MonorepoTool.CARGO)
        if not tools:
            tools.add(MonorepoTool.NONE)
        return tools

    def _resolve_package_globs(
        self,
        root: Path,
        root_names: set[str],
        tools: set[MonorepoTool],
    ) -> list[str]:
        globs: list[str] = []
        if MonorepoTool.PNPM in tools:
            globs.extend(self._parse_pnpm_workspace(root / "pnpm-workspace.yaml"))
        pkg = root / "package.json"
        if pkg.is_file() and (
            MonorepoTool.NPM_WORKSPACES in tools or MonorepoTool.PNPM in tools
        ):
            data = self._read_json(pkg)
            ws = data.get("workspaces")
            if isinstance(ws, list):
                globs.extend(str(x) for x in ws if isinstance(x, str))
            elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
                globs.extend(str(x) for x in ws["packages"] if isinstance(x, str))
        if MonorepoTool.NX in tools or MonorepoTool.TURBO in tools or MonorepoTool.LERNA in tools:
            globs.extend(["apps/*", "packages/*", "services/*"])
        if MonorepoTool.CARGO in tools:
            cargo = (root / "Cargo.toml").read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r'members\s*=\s*\[([^\]]*)\]', cargo, re.S):
                for item in re.findall(r'"([^"]+)"', match.group(1)):
                    globs.append(item)
        if MonorepoTool.GO_WORK in tools:
            text = (root / "go.work").read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("go ") and not line.startswith("//"):
                    if line.startswith("./") or "/" in line or line not in {"use", "(" , ")"}:
                        cleaned = line.strip("./")
                        if cleaned and cleaned not in {"use", "(", ")"}:
                            globs.append(cleaned)
        # Deduplicate while preserving order
        seen: set[str] = set()
        out: list[str] = []
        for g in globs:
            if g not in seen:
                seen.add(g)
                out.append(g)
        if not out and (MonorepoTool.NONE not in tools or (root / "apps").is_dir()):
            out = ["apps/*", "packages/*", "services/*"]
        return out

    def _discover_packages(self, root: Path, globs: list[str], tools: set[MonorepoTool]) -> list[Path]:
        found: list[Path] = []
        for pattern in globs:
            # Support simple patterns like apps/*, packages/web
            if "*" in pattern:
                base, _, _ = pattern.partition("*")
                base_dir = root / base.rstrip("/")
                if not base_dir.is_dir():
                    continue
                for child in sorted(base_dir.iterdir()):
                    if child.is_dir() and child.name not in _SKIP_DIRS and self._looks_like_package(child):
                        found.append(child)
            else:
                candidate = root / pattern
                if candidate.is_dir() and self._looks_like_package(candidate):
                    found.append(candidate)
        # Unique by resolved path
        uniq: dict[Path, Path] = {}
        for p in found:
            uniq[p.resolve()] = p
        return list(uniq.values())

    def _looks_like_package(self, path: Path) -> bool:
        markers = (
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "go.mod",
            "Cargo.toml",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "Dockerfile",
        )
        return any((path / m).is_file() for m in markers)

    def _classify_package(self, repo_root: Path, pkg_dir: Path) -> DetectedService | None:
        rel = "." if pkg_dir.resolve() == repo_root.resolve() else str(pkg_dir.relative_to(repo_root)).replace("\\", "/")
        pkg_id = re.sub(r"[^a-z0-9-]+", "-", rel.lower()).strip("-") or "root"
        dockerfile = self._find_dockerfile(pkg_dir)
        markers: list[str] = []

        if (pkg_dir / "package.json").is_file():
            markers.append("package.json")
            return self._classify_node(pkg_id, rel, pkg_dir, dockerfile, markers)

        if (pkg_dir / "pyproject.toml").is_file() or (pkg_dir / "requirements.txt").is_file():
            if (pkg_dir / "pyproject.toml").is_file():
                markers.append("pyproject.toml")
            if (pkg_dir / "requirements.txt").is_file():
                markers.append("requirements.txt")
            return self._classify_python(pkg_id, rel, pkg_dir, dockerfile, markers)

        if (pkg_dir / "go.mod").is_file():
            markers.append("go.mod")
            framework = "gin" if self._file_mentions(pkg_dir, "github.com/gin-gonic/gin") else "go"
            return DetectedService(
                id=pkg_id,
                name=f"launch-{pkg_id}"[:63],
                path=rel,
                role=ServiceRole.API,
                framework=framework,
                runtime="go",
                port=_PORT_BY_FRAMEWORK.get(framework, 8080),
                has_dockerfile=dockerfile is not None,
                dockerfile_path=dockerfile,
                health_path="/health",
                markers=markers,
            )

        if (pkg_dir / "Cargo.toml").is_file():
            markers.append("Cargo.toml")
            return DetectedService(
                id=pkg_id,
                name=f"launch-{pkg_id}"[:63],
                path=rel,
                role=ServiceRole.API,
                framework="rust",
                runtime="rust",
                port=8080,
                has_dockerfile=dockerfile is not None,
                dockerfile_path=dockerfile,
                health_path="/health",
                markers=markers,
            )

        if (pkg_dir / "pom.xml").is_file() or (pkg_dir / "build.gradle").is_file() or (pkg_dir / "build.gradle.kts").is_file():
            markers.append("java")
            return DetectedService(
                id=pkg_id,
                name=f"launch-{pkg_id}"[:63],
                path=rel,
                role=ServiceRole.API,
                framework="springboot",
                runtime="java",
                port=8080,
                has_dockerfile=dockerfile is not None,
                dockerfile_path=dockerfile,
                health_path="/actuator/health",
                markers=markers,
            )

        if dockerfile:
            return DetectedService(
                id=pkg_id,
                name=f"launch-{pkg_id}"[:63],
                path=rel,
                role=ServiceRole.UNKNOWN,
                framework="generic",
                runtime="container",
                port=8080,
                has_dockerfile=True,
                dockerfile_path=dockerfile,
                markers=["Dockerfile"],
            )
        return None

    def _classify_node(
        self,
        pkg_id: str,
        rel: str,
        pkg_dir: Path,
        dockerfile: str | None,
        markers: list[str],
    ) -> DetectedService:
        data = self._read_json(pkg_dir / "package.json")
        deps: dict[str, object] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            block = data.get(key)
            if isinstance(block, dict):
                deps.update(block)
        dep_names = {str(k).lower() for k in deps}

        framework = "node"
        port = 3000
        role = ServiceRole.UNKNOWN
        health = "/"

        for dep, (fw, p, hp) in _WEB_DEPS.items():
            if dep in dep_names or any(dep in d for d in dep_names):
                framework, port, health = fw, p, hp
                role = ServiceRole.WEB
                break
        if role == ServiceRole.UNKNOWN:
            for dep, (fw, p, hp) in _API_DEPS.items():
                if dep in dep_names or any(dep in d for d in dep_names):
                    framework, port, health = fw, p, hp
                    role = ServiceRole.API
                    break

        # Heuristic from path name
        if role == ServiceRole.UNKNOWN:
            lower = rel.lower()
            if any(x in lower for x in ("web", "frontend", "ui", "client", "app")):
                role = ServiceRole.WEB
                framework = "vite" if "vite" in dep_names else "node"
            elif any(x in lower for x in ("api", "server", "backend", "service")):
                role = ServiceRole.API
                framework = "express" if "express" in dep_names else "node"

        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        port = self._port_from_scripts(scripts, port)
        name_hint = str(data.get("name") or pkg_id).split("/")[-1]
        slug = re.sub(r"[^a-z0-9-]+", "-", name_hint.lower()).strip("-") or pkg_id

        return DetectedService(
            id=pkg_id,
            name=f"launch-{slug}"[:63],
            path=rel,
            role=role,
            framework=framework,
            runtime="node",
            port=port,
            has_dockerfile=dockerfile is not None,
            dockerfile_path=dockerfile,
            health_path=health,
            markers=markers,
            env_hints={"NODE_ENV": "production"},
        )

    def _classify_python(
        self,
        pkg_id: str,
        rel: str,
        pkg_dir: Path,
        dockerfile: str | None,
        markers: list[str],
    ) -> DetectedService:
        text = ""
        for name in ("pyproject.toml", "requirements.txt"):
            path = pkg_dir / name
            if path.is_file():
                text += path.read_text(encoding="utf-8", errors="replace").lower()
        framework = "python"
        port = 8000
        health = "/health"
        if "fastapi" in text:
            framework, port = "fastapi", 8000
        elif "flask" in text:
            framework, port = "flask", 5000
        elif "django" in text:
            framework, port = "django", 8000
            health = "/healthz"
        return DetectedService(
            id=pkg_id,
            name=f"launch-{pkg_id}"[:63],
            path=rel,
            role=ServiceRole.API,
            framework=framework,
            runtime="python",
            port=port,
            has_dockerfile=dockerfile is not None,
            dockerfile_path=dockerfile,
            health_path=health,
            markers=markers,
        )

    def _assign_names_and_preview(self, services: list[DetectedService]) -> list[DetectedService]:
        used: set[str] = set()
        web_idxs = [i for i, s in enumerate(services) if s.role == ServiceRole.WEB]
        api_idxs = [i for i, s in enumerate(services) if s.role == ServiceRole.API]
        preview_idx = web_idxs[0] if web_idxs else (api_idxs[0] if api_idxs else 0)

        out: list[DetectedService] = []
        for i, svc in enumerate(services):
            name = svc.name
            base = name
            n = 2
            while name in used:
                name = f"{base}-{n}"[:63]
                n += 1
            used.add(name)
            # Prefer conventional names for first web/api
            if i in web_idxs and web_idxs.index(i) == 0 and "launch-web" not in used:
                if name != "launch-web":
                    used.discard(name)
                    name = "launch-web"
                    used.add(name)
            elif i in api_idxs and api_idxs.index(i) == 0 and "launch-server" not in used:
                if name != "launch-server":
                    used.discard(name)
                    name = "launch-server"
                    used.add(name)
            out.append(
                svc.model_copy(
                    update={
                        "name": name,
                        "is_preview_target": i == preview_idx,
                    }
                )
            )
        return out

    def _detect_datastores(self, root: Path) -> list[str]:
        found: set[str] = set()
        # Prisma / Drizzle
        for path in root.rglob("schema.prisma"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if "provider" in text and "postgresql" in text:
                found.add("postgres")
            if "mysql" in text:
                found.add("mysql")
            if "mongodb" in text or "mongo" in text:
                found.add("mongodb")
        for name in ("drizzle.config.ts", "drizzle.config.js", "drizzle.config.mjs"):
            if (root / name).is_file() or any(root.rglob(name)):
                found.add("postgres")
        for compose_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml"):
            compose = root / compose_name
            if not compose.is_file():
                continue
            text = compose.read_text(encoding="utf-8", errors="replace").lower()
            if "postgres" in text or "postgresql" in text:
                found.add("postgres")
            if "redis" in text:
                found.add("redis")
            if "mongo" in text:
                found.add("mongodb")
        # package.json deps
        for pkg in root.rglob("package.json"):
            if any(part in _SKIP_DIRS for part in pkg.parts):
                continue
            try:
                data = self._read_json(pkg)
            except Exception:
                continue
            blob = json.dumps(data).lower()
            if "ioredis" in blob or "\"redis\"" in blob:
                found.add("redis")
            if "pg" in blob or "postgres" in blob or "prisma" in blob:
                found.add("postgres")
            if "mongodb" in blob or "mongoose" in blob:
                found.add("mongodb")
        return sorted(found)

    @staticmethod
    def _find_dockerfile(pkg_dir: Path) -> str | None:
        for name in ("Dockerfile", "dockerfile", "Containerfile"):
            if (pkg_dir / name).is_file():
                return name
        dockers = pkg_dir / "dockers"
        if dockers.is_dir():
            for path in sorted(dockers.glob("Dockerfile*")):
                if path.is_file():
                    return str(path.relative_to(pkg_dir)).replace("\\", "/")
        return None

    @staticmethod
    def _parse_pnpm_workspace(path: Path) -> list[str]:
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        packages: list[str] = []
        in_packages = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("packages:"):
                in_packages = True
                continue
            if in_packages:
                if stripped.startswith("-"):
                    item = stripped.lstrip("-").strip().strip("'\"")
                    if item:
                        packages.append(item)
                elif stripped and not stripped.startswith("#") and ":" in stripped:
                    break
        return packages

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _port_from_scripts(scripts: dict[str, object], default: int) -> int:
        for value in scripts.values():
            if not isinstance(value, str):
                continue
            match = re.search(r"(?:--port|-p|=|:)\s*(\d{2,5})", value)
            if match:
                port = int(match.group(1))
                if 1 <= port <= 65535:
                    return port
        return default

    @staticmethod
    def _file_mentions(pkg_dir: Path, needle: str) -> bool:
        go_mod = pkg_dir / "go.mod"
        if go_mod.is_file() and needle in go_mod.read_text(encoding="utf-8", errors="replace"):
            return True
        return False
