"""Local Compose / running-instance IaC stubs (Terraform / Pulumi).

Cloud Terraform modules do not apply to ``LocalCloudConfig``. These stubs give
operators a real engine tree they can evolve when promoting to a cloud provider,
plus documented ``null_resource`` / Pulumi placeholders that ``terraform init``
or ``pulumi preview`` can exercise locally.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.cloud import AnsibleConfig, IaCEngine, WorkspaceRuntimeMode

TF_ROOT = Path("infra") / "terraform"


def write_local_runtime_iac(
    workspace_dir: Path,
    *,
    name: str,
    engine: IaCEngine,
    runtime_mode: WorkspaceRuntimeMode,
    ansible: AnsibleConfig | None = None,
    listen_port: int | None = None,
) -> list[str]:
    """Write Terraform/OpenTofu, Pulumi, or Ansible stubs for local non-K8s runtimes."""
    if engine in {IaCEngine.TERRAFORM, IaCEngine.OPENTOFU}:
        return _write_local_terraform(workspace_dir, name=name, runtime_mode=runtime_mode)
    if engine == IaCEngine.PULUMI:
        return _write_local_pulumi(workspace_dir, name=name, runtime_mode=runtime_mode)
    if engine == IaCEngine.ANSIBLE:
        from app.services.ansible_scaffold import write_ansible_scaffold

        cfg = ansible or AnsibleConfig(enabled=True)
        if not cfg.enabled:
            cfg = cfg.model_copy(update={"enabled": True})
        return write_ansible_scaffold(
            workspace_dir,
            name=name,
            config=cfg,
            runtime_mode=runtime_mode,
            listen_port=listen_port,
        )
    raise ValueError(f"Unsupported IaC engine for local runtime: {engine!r}")


def _runtime_label(runtime_mode: WorkspaceRuntimeMode) -> str:
    if runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
        return "docker_compose"
    return "running_instance"


def _write_local_terraform(
    workspace_dir: Path,
    *,
    name: str,
    runtime_mode: WorkspaceRuntimeMode,
) -> list[str]:
    written: list[str] = []
    label = _runtime_label(runtime_mode)

    def _write(relative: Path, content: str) -> None:
        path = workspace_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(relative).replace("\\", "/"))

    _write(
        TF_ROOT / "providers.tf",
        'terraform {\n  required_version = ">= 1.5.0"\n'
        '  required_providers {\n'
        '    null = {\n'
        '      source  = "hashicorp/null"\n'
        '      version = "~> 3.2"\n'
        "    }\n"
        "  }\n"
        "}\n\n"
        'provider "null" {}\n',
    )
    _write(
        TF_ROOT / "variables.tf",
        'variable "environment_name" {\n'
        "  type        = string\n"
        '  description = "Launchpad workspace / environment name"\n'
        "}\n\n"
        'variable "runtime_mode" {\n'
        "  type        = string\n"
        f'  default     = "{label}"\n'
        '  description = "Local runtime: docker_compose or running_instance"\n'
        "}\n",
    )
    _write(
        TF_ROOT / "terraform.tfvars",
        f'environment_name = "{name}"\n'
        f'runtime_mode     = "{label}"\n',
    )
    howto = (
        "docker compose up --build"
        if runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE
        else "docker run (or SSH/serverless deploy via Launchpad attach)"
    )
    _write(
        TF_ROOT / "main.tf",
        "resource \"null_resource\" \"local_runtime\" {\n"
        "  triggers = {\n"
        "    environment_name = var.environment_name\n"
        "    runtime_mode     = var.runtime_mode\n"
        "  }\n\n"
        "  provisioner \"local-exec\" {\n"
        f'    command = "echo Launchpad local runtime ready - use: {howto}"\n'
        "  }\n"
        "}\n",
    )
    _write(
        TF_ROOT / "outputs.tf",
        'output "environment_name" {\n'
        "  value       = var.environment_name\n"
        '  description = "Workspace name"\n'
        "}\n\n"
        'output "runtime_mode" {\n'
        "  value       = var.runtime_mode\n"
        '  description = "Local runtime mode"\n'
        "}\n",
    )
    _write(
        TF_ROOT / ".gitignore",
        "*.tfstate\n*.tfstate.*\n.terraform/\n.terraform.lock.hcl\ncrash.log\n",
    )
    _write(
        TF_ROOT / "README.md",
        f"# Local {label} IaC\n\n"
        "This stub is for local Docker Compose / running-instance workspaces.\n"
        "Promote to a cloud provider in **Provision** to generate real "
        "GCP/AWS/Azure modules.\n\n"
        "```bash\n"
        "cd infra/terraform\n"
        "terraform init\n"
        "terraform apply\n"
        "```\n",
    )
    return written


def _write_local_pulumi(
    workspace_dir: Path,
    *,
    name: str,
    runtime_mode: WorkspaceRuntimeMode,
) -> list[str]:
    written: list[str] = []
    label = _runtime_label(runtime_mode)

    def _write(relative: str, content: str) -> None:
        path = workspace_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(relative)

    _write(
        "Pulumi.yaml",
        f"name: {name}\n"
        "runtime: nodejs\n"
        f"description: Launchpad local {label} workspace for {name}\n",
    )
    _write(
        "Pulumi.dev.yaml",
        "config:\n"
        f"  environmentName: {name}\n"
        f"  runtimeMode: {label}\n",
    )
    package_json = json.dumps(
        {
            "name": name,
            "main": "index.ts",
            "devDependencies": {
                "typescript": "^5.6.0",
                "@types/node": "^22.7.0",
            },
            "dependencies": {
                "@pulumi/pulumi": "^3.137.0",
            },
        },
        indent=2,
    )
    _write("package.json", package_json + "\n")
    _write(
        "tsconfig.json",
        json.dumps(
            {
                "compilerOptions": {
                    "strict": True,
                    "outDir": "bin",
                    "target": "es2020",
                    "module": "commonjs",
                    "moduleResolution": "node",
                    "skipLibCheck": True,
                },
                "files": ["index.ts"],
            },
            indent=2,
        )
        + "\n",
    )
    _write(
        "index.ts",
        "import * as pulumi from '@pulumi/pulumi';\n\n"
        "const config = new pulumi.Config();\n"
        f"const environmentName = config.get('environmentName') ?? {json.dumps(name)};\n"
        f"const runtimeMode = config.get('runtimeMode') ?? {json.dumps(label)};\n\n"
        "export const launchpadLocalRuntime = {\n"
        "  environmentName,\n"
        "  runtimeMode,\n"
        "  note: 'Local Compose / running-instance stub - promote to cloud for real stacks',\n"
        "};\n",
    )
    _write(".gitignore", "node_modules/\nbin/\n*.tsbuildinfo\n")
    _write(
        "infra/pulumi/README.md",
        f"# Local {label} Pulumi\n\n"
        "Stub stack for local Docker Compose / running-instance workspaces.\n"
        "Promote to a cloud provider in **Provision** for managed resources.\n",
    )
    return written
