"""Orchestrate scaffold-driven cloud deploy: IaC apply → Ansible → preview URL.

Deploy to cloud uses the generated workspace tree (Terraform/OpenTofu/Pulumi +
Ansible), not imperative cloud SDK create paths. The platform injects
credentials, streams logs via the worker, and maps outputs into
``ProvisionedResources``.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.cloud import (
    AnsibleAppDeployMode,
    AnsibleConfig,
    CloudCredentials,
    CloudProvider,
    InstanceCodeSource,
    InstanceProcessStrategy,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
)
from app.services.ansible_runner import run_ansible_site, update_ansible_inventory_host
from app.services.iac_apply import parse_preview_fields, run_workspace_iac_apply
from app.services.instance_process_scaffold import ansible_deploy_mode_for_strategy
from app.services.kubernetes import ProvisionedResources

logger = get_logger(__name__)


class ScaffoldCloudDeployError(RuntimeError):
    """Scaffold IaC/Ansible deploy failed."""


def _request_from_wizard_snapshot(raw: dict[str, object]):
    """Rebuild a wizard request from ``.launchpad/wizard.json`` (best effort)."""
    from pydantic import TypeAdapter

    from app.schemas.cloud import (
        AnsibleConfig,
        CloudConfig,
        ContainerScaffoldConfig,
        CostOptimizationConfig,
        IaCEngine,
        KubernetesPackaging,
        KubernetesWorkloadOptions,
        ProvisioningWizardRequest,
        RunningInstanceConfig,
        WorkloadDependenciesConfig,
        WorkspaceArtifactsMode,
        WorkspaceRuntimeMode,
    )

    try:
        cloud_raw = raw.get("cloud")
        if not isinstance(cloud_raw, dict):
            return None
        cloud = TypeAdapter(CloudConfig).validate_python(cloud_raw)
        engine = IaCEngine(str(raw.get("iac_engine") or "terraform"))
        runtime = WorkspaceRuntimeMode(str(raw.get("runtime_mode") or "kubernetes"))
        artifact = WorkspaceArtifactsMode(str(raw.get("artifact_mode") or "iac_only"))
        packaging = KubernetesPackaging(str(raw.get("kubernetes_packaging") or "none"))
        ri_raw = raw.get("running_instance")
        ri = (
            RunningInstanceConfig.model_validate(ri_raw)
            if isinstance(ri_raw, dict)
            else RunningInstanceConfig()
        )
        return ProvisioningWizardRequest(
            name=str(raw.get("name") or "workspace"),
            iac_engine=engine,
            cloud=cloud,
            run_init=bool(raw.get("run_init", True)),
            runtime_mode=runtime,
            running_instance=ri,
            artifact_mode=artifact,
            kubernetes_packaging=packaging,
            kubernetes_options=KubernetesWorkloadOptions.model_validate(
                raw.get("kubernetes_options") or {}
            ),
            cost_optimization=CostOptimizationConfig.model_validate(
                raw.get("cost_optimization") or {}
            ),
            container_scaffold=ContainerScaffoldConfig.model_validate(
                raw.get("container_scaffold") or {}
            ),
            dependencies=WorkloadDependenciesConfig.model_validate(
                raw.get("dependencies") or {}
            ),
            ansible=AnsibleConfig.model_validate(raw.get("ansible") or {}),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort heal path
        logger.warning("scaffold_wizard_rebuild_failed", error=str(exc))
        return None


def ensure_scaffold_vm_compute(workspace_root: Path) -> bool:
    """Enable GCE/EC2 in the workspace scaffold when VM auto-create is implied.

    Returns True when the on-disk IaC was regenerated.
    """
    from app.schemas.cloud import (
        AwsCloudConfig,
        GcpCloudConfig,
        RunningInstanceKind,
        WorkspaceRuntimeMode,
    )
    from app.services.iac_generator import IaCGenerator
    from app.services.runtime_mode import ensure_autocreate_vm_resources

    gen = IaCGenerator()
    raw = gen.read_wizard_snapshot(workspace_root)
    if not raw:
        return False
    request = _request_from_wizard_snapshot(raw)
    if request is None:
        return False
    if request.runtime_mode != WorkspaceRuntimeMode.RUNNING_INSTANCE:
        return False
    if request.running_instance.kind != RunningInstanceKind.VM:
        return False

    before = request.cloud.model_dump(mode="json")
    before_ansible = request.ansible.model_dump(mode="json")
    patched_cloud = ensure_autocreate_vm_resources(
        request.cloud,
        runtime_mode=request.runtime_mode,
        running_instance=request.running_instance,
    )
    from app.services.runtime_mode import ensure_ansible_for_vm_runtime

    patched_ansible = ensure_ansible_for_vm_runtime(
        request.ansible,
        runtime_mode=request.runtime_mode,
        running_instance=request.running_instance,
    )
    request = request.model_copy(
        update={"cloud": patched_cloud, "ansible": patched_ansible}
    )

    needs_vm = False
    if isinstance(request.cloud, GcpCloudConfig):
        needs_vm = request.cloud.resources.compute_instance
    elif isinstance(request.cloud, AwsCloudConfig):
        needs_vm = request.cloud.resources.ec2
    if not needs_vm:
        return False

    pulumi_index = workspace_root / "infra" / "pulumi" / "index.ts"
    tf_main = workspace_root / "infra" / "terraform" / "modules" / "cluster" / "main.tf"
    ansible_site = workspace_root / "infra" / "ansible" / "playbooks" / "site.yml"
    engine = str(request.iac_engine.value)
    already = False
    if engine == "pulumi" and pulumi_index.is_file():
        pulumi_text = pulumi_index.read_text(encoding="utf-8")
        # Require modern scaffold (explicit provider + public_ip) so older Pulumi
        # trees without VM / project wiring are regenerated.
        already = (
            "export const public_ip" in pulumi_text
            and "gcp.Provider" in pulumi_text
            and "gcp.compute.Instance" in pulumi_text
        )
    elif engine in {"terraform", "opentofu"} and tf_main.is_file():
        already = "google_compute_instance" in tf_main.read_text(encoding="utf-8")
    ansible_ready = ansible_site.is_file()
    cloud_unchanged = before == request.cloud.model_dump(mode="json")
    ansible_unchanged = before_ansible == request.ansible.model_dump(mode="json")
    if already and ansible_ready and cloud_unchanged and ansible_unchanged:
        return False

    gen.regenerate(workspace_root, request)
    logger.info(
        "scaffold_vm_compute_ensured",
        workspace_root=str(workspace_root),
        provider=request.cloud.provider.value,
        ansible=ansible_site.is_file(),
    )
    return True


def should_use_scaffold_cloud_deploy(
    *,
    cloud_provider: str | None,
    running_instance: RunningInstanceConfig | None,
    settings: Settings | None = None,
) -> bool:
    """True when cloud ATTACH should run workspace IaC instead of attach_deploy APIs."""
    cfg = settings or get_settings()
    if not getattr(cfg, "scaffold_cloud_deploy_enabled", True):
        return False
    provider = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()
    if provider == CloudProvider.LOCAL.value:
        return False
    kind = (running_instance.kind if running_instance else RunningInstanceKind.VM)
    if kind == RunningInstanceKind.LOCAL_MACHINE:
        return False
    return kind in {RunningInstanceKind.VM, RunningInstanceKind.SERVERLESS}


def teardown_via_scaffold(
    *,
    workspace_root: Path | None,
    engine: str,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    cloud_provider: str | None,
    running_instance: RunningInstanceConfig | None,
    settings: Settings | None = None,
    sibling_active_envs: int = 0,
) -> tuple[bool, str]:
    """Destroy scaffold-applied cloud infra for an environment's workspace.

    Returns ``(handled, detail)``. When ``handled`` is True, the caller should
    skip imperative ``teardown_attach`` for cloud VM/serverless resources.
    """
    from app.services.iac_destroy import run_workspace_iac_destroy

    cfg = settings or get_settings()
    if not should_use_scaffold_cloud_deploy(
        cloud_provider=cloud_provider,
        running_instance=running_instance,
        settings=cfg,
    ):
        return False, "scaffold teardown not applicable"

    if sibling_active_envs > 0:
        logger.info(
            "scaffold_teardown_skipped_shared_workspace",
            workspace_id=workspace_id,
            sibling_active_envs=sibling_active_envs,
        )
        return (
            False,
            f"workspace has {sibling_active_envs} other active environment(s); "
            "skipping shared IaC destroy",
        )

    if workspace_root is None or not Path(workspace_root).is_dir():
        return False, "workspace root missing for scaffold teardown"

    result = run_workspace_iac_destroy(
        root_dir=str(workspace_root),
        engine=engine,
        credentials=credentials,
        org_id=org_id,
        workspace_id=workspace_id,
        settings=cfg,
    )
    if result.status == "destroyed":
        logger.info(
            "scaffold_teardown_destroyed",
            workspace_id=workspace_id,
            detail=result.detail,
        )
        return True, result.detail or "IaC destroy complete"
    if result.status == "skipped":
        logger.info(
            "scaffold_teardown_skipped",
            workspace_id=workspace_id,
            detail=result.detail,
        )
        return False, result.detail or "IaC destroy skipped"
    logger.warning(
        "scaffold_teardown_failed",
        workspace_id=workspace_id,
        detail=result.detail,
    )
    return False, result.detail or "IaC destroy failed"


def ansible_config_for_runtime(
    *,
    source: AnsibleConfig | None,
    runtime_mode: WorkspaceRuntimeMode | None,
    running_instance: RunningInstanceConfig | None,
) -> AnsibleConfig:
    """Enable Ansible for VM/compose hosts with deploy mode matching process strategy."""
    base = source or AnsibleConfig()
    updates: dict[str, object] = {"enabled": True}
    listen = int(base.app_listen_port)
    if runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
        updates["app_deploy_mode"] = AnsibleAppDeployMode.DOCKER_COMPOSE
        updates["install_docker"] = True
        updates["install_compose_plugin"] = True
    elif running_instance is not None:
        mode = ansible_deploy_mode_for_strategy(running_instance.process_strategy)
        try:
            updates["app_deploy_mode"] = AnsibleAppDeployMode(mode)
        except ValueError:
            updates["app_deploy_mode"] = AnsibleAppDeployMode.DOCKER_RUN
        if running_instance.process_strategy == InstanceProcessStrategy.DOCKER:
            updates["install_docker"] = True
        if running_instance.listen_port:
            listen = int(running_instance.listen_port)
            updates["app_listen_port"] = listen
        if running_instance.ssh_user:
            updates["ssh_user"] = running_instance.ssh_user
    ufw = sorted(
        {
            int(p)
            for p in (*base.ufw_allow_ports, 22, listen)
            if 1 <= int(p) <= 65535
        }
    )
    updates["ufw_allow_ports"] = ufw
    return base.model_copy(update=updates)


def deploy_via_scaffold(
    *,
    workspace_root: Path,
    engine: str,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    environment_id: str,
    environment_name: str,
    namespace: str,
    running_instance: RunningInstanceConfig,
    settings: Settings | None = None,
    ssh_private_key_path: str | None = None,
    tf_vars: dict[str, str] | None = None,
    run_ansible: bool = True,
    git_repo_url: str = "",
    git_branch: str = "main",
    image: str | None = None,
    cloud_provider: str | None = None,
) -> ProvisionedResources:
    """Apply scaffolded IaC, configure the VM (Ansible or SSH fallback), return resources."""
    from app.services.attach_deploy import AttachDeployError, _deploy_vm
    from app.services.preview_ssh import ensure_preview_ssh_keypair

    cfg = settings or get_settings()
    root = workspace_root.resolve()
    if not root.is_dir():
        raise ScaffoldCloudDeployError(f"Workspace root missing: {root}")

    # Heal workspaces created before GCP VM auto-create set compute_instance.
    try:
        ensure_scaffold_vm_compute(root)
    except Exception as exc:  # noqa: BLE001 - proceed; apply may still fail clearly
        logger.warning(
            "scaffold_vm_compute_ensure_failed",
            workspace_root=str(root),
            error=str(exc),
        )

    key_path, public_key = ensure_preview_ssh_keypair(environment_id, settings=cfg)
    instance_with_ssh = running_instance.model_copy(
        update={
            "ssh_key_path": (
                ssh_private_key_path
                or running_instance.ssh_key_path
                or key_path
            ),
            "ssh_user": running_instance.ssh_user or "ubuntu",
        }
    )
    merged_tf = dict(tf_vars or {})
    merged_tf.setdefault("ssh_public_key", public_key)
    merged_tf.setdefault(
        "app_listen_port",
        str(instance_with_ssh.listen_port or 8080),
    )
    if credentials is not None and not merged_tf.get("project_id"):
        from app.core.secrets import project_id_from_gcp_sa_json

        project = project_id_from_gcp_sa_json(
            credentials.gcp_sa_key_json
        ) or (credentials.gcp_project_id or "").strip()
        if project:
            merged_tf["project_id"] = project

    # Stale workspaces may still enable OS Login, which ignores metadata ssh-keys.
    from app.services.gcp_vm_ssh import (
        ensure_gcp_instance_ssh_metadata,
        patch_workspace_disable_os_login,
    )

    patch_workspace_disable_os_login(root)

    apply = run_workspace_iac_apply(
        root_dir=str(root),
        engine=engine,
        credentials=credentials,
        org_id=org_id,
        workspace_id=workspace_id,
        settings=cfg,
        tf_vars=merged_tf,
    )
    if apply.status == "failed":
        raise ScaffoldCloudDeployError(
            f"IaC apply failed: {apply.detail}. {apply.output[-800:]}"
        )
    if apply.status == "skipped":
        raise ScaffoldCloudDeployError(
            f"IaC apply skipped: {apply.detail}. Install terraform/tofu/pulumi "
            "or enable scaffold resources in the workspace."
        )

    fields = parse_preview_fields(apply.outputs)
    public_ip = fields.get("public_ip")
    preview_url = fields.get("preview_url")
    listen_port = int(fields.get("listen_port") or instance_with_ssh.listen_port or 8080)

    # Prefer zone + GCE name from Terraform compute_instance_id (not wizard region).
    instance_updates: dict[str, object] = {
        "host": public_ip or instance_with_ssh.host,
        "listen_port": listen_port,
        "service_name": (
            fields.get("instance_name")
            or instance_with_ssh.service_name
            or environment_name
        ),
    }
    if fields.get("instance_zone"):
        instance_updates["region"] = fields["instance_zone"]
    updated_instance = instance_with_ssh.model_copy(update=instance_updates)

    provider = (cloud_provider or "").strip().lower()
    if (
        provider == CloudProvider.GCP.value
        and fields.get("instance_name")
        and fields.get("instance_zone")
    ):
        project = None
        from app.services.cloud_instance_compute import parse_gcp_compute_instance_id

        parsed = parse_gcp_compute_instance_id(fields.get("instance_id"))
        if parsed:
            project = parsed[0]
        try:
            ensure_gcp_instance_ssh_metadata(
                instance_name=str(fields["instance_name"]),
                zone=str(fields["instance_zone"]),
                public_key_line=public_key,
                environment_id=environment_id,
                credentials=credentials,
                ssh_user=updated_instance.ssh_user or "ubuntu",
                project_id=project,
            )
        except Exception as exc:
            logger.warning(
                "gcp_ssh_metadata_update_failed",
                environment_id=environment_id,
                detail=str(exc)[-500:],
            )
            # Terraform apply after HCL patch usually already set enable-oslogin=FALSE
            # + ssh-keys; continue and let SSH probe decide.

    notice = f"Scaffold deploy: IaC {apply.status}"
    if (
        run_ansible
        and updated_instance.kind == RunningInstanceKind.VM
        and public_ip
    ):
        site = root / "infra" / "ansible" / "playbooks" / "site.yml"
        ansible_ok = False
        ansible_detail = "ansible not attempted"
        if site.is_file():
            update_ansible_inventory_host(
                root,
                host=public_ip,
                ssh_user=updated_instance.ssh_user or "ubuntu",
                ssh_port=int(updated_instance.ssh_port or 22),
                ssh_private_key_path=updated_instance.ssh_key_path,
            )
            try:
                ansible = run_ansible_site(
                    root,
                    timeout_seconds=float(
                        getattr(cfg, "ansible_deploy_timeout_seconds", 900)
                    ),
                )
                ansible_detail = ansible.detail or ansible.status
                ansible_ok = ansible.status == "applied"
                if ansible_ok:
                    notice = (
                        f"Scaffold deploy: IaC {apply.status}, Ansible applied"
                    )
                elif ansible.status == "failed":
                    logger.warning(
                        "scaffold_ansible_failed_trying_ssh",
                        environment_id=environment_id,
                        detail=ansible.detail,
                    )
                else:
                    logger.warning(
                        "scaffold_ansible_skipped_trying_ssh",
                        environment_id=environment_id,
                        detail=ansible.detail,
                    )
            except Exception as exc:  # noqa: BLE001 - never hard-fail configure
                ansible_detail = f"ansible exception: {exc}"
                logger.warning(
                    "scaffold_ansible_exception_trying_ssh",
                    environment_id=environment_id,
                    detail=str(exc)[-500:],
                )
        else:
            # Heal missing playbooks on older workspaces mid-deploy.
            if ensure_scaffold_vm_compute(root):
                site = root / "infra" / "ansible" / "playbooks" / "site.yml"
            if site.is_file():
                update_ansible_inventory_host(
                    root,
                    host=public_ip,
                    ssh_user=updated_instance.ssh_user or "ubuntu",
                    ssh_port=int(updated_instance.ssh_port or 22),
                    ssh_private_key_path=updated_instance.ssh_key_path,
                )
                try:
                    ansible = run_ansible_site(
                        root,
                        timeout_seconds=float(
                            getattr(cfg, "ansible_deploy_timeout_seconds", 900)
                        ),
                    )
                    ansible_detail = ansible.detail or ansible.status
                    ansible_ok = ansible.status == "applied"
                except Exception as exc:  # noqa: BLE001 - never hard-fail configure
                    ansible_detail = f"ansible exception: {exc}"
                    logger.warning(
                        "scaffold_ansible_exception_trying_ssh",
                        environment_id=environment_id,
                        detail=str(exc)[-500:],
                    )
        if not ansible_ok:
            try:
                from app.services.git_urls import is_remote_cloneable_git_url

                # Linked remote repos clone on the VM. Import/scaffold trees sync over SSH.
                # Do not force SSH when a cloneable git_repo_url is present.
                deploy_instance = updated_instance
                if is_remote_cloneable_git_url(git_repo_url):
                    deploy_instance = updated_instance.model_copy(
                        update={"code_source": InstanceCodeSource.GITHUB}
                    )
                elif root is not None:
                    deploy_instance = updated_instance.model_copy(
                        update={"code_source": InstanceCodeSource.SSH}
                    )
                attach_result = _deploy_vm(
                    environment_id=environment_id,
                    name=environment_name,
                    image=image,
                    running_instance=deploy_instance,
                    settings=cfg,
                    cloud_provider=cloud_provider,
                    credentials=credentials,
                    workspace_root=root,
                    git_repo_url=git_repo_url,
                    git_branch=git_branch or "main",
                )
            except AttachDeployError as exc:
                # IaC already created the VM (and public_ip). Do not fail the whole
                # provision: surface Open App with the preview URL and let the user
                # retry app configure. Terraform path previously looked "working"
                # mainly because apply succeeded; SSH flakiness should not hide that.
                if public_ip or preview_url:
                    logger.warning(
                        "scaffold_vm_configure_deferred",
                        environment_id=environment_id,
                        host=public_ip,
                        detail=str(exc)[-800:],
                    )
                    notice = (
                        f"Scaffold deploy: IaC {apply.status}; "
                        f"VM is up but app configure deferred "
                        f"(Ansible: {ansible_detail}). SSH: {exc}"
                    )
                else:
                    raise ScaffoldCloudDeployError(
                        f"IaC applied but VM app configure failed "
                        f"(Ansible: {ansible_detail}). SSH fallback: {exc}"
                    ) from exc
            else:
                if attach_result.preview_url:
                    preview_url = attach_result.preview_url
                if attach_result.running_instance is not None:
                    updated_instance = attach_result.running_instance
                notice = (
                    f"Scaffold deploy: IaC {apply.status}; "
                    f"app configured via SSH ({ansible_detail})"
                )
        else:
            # Ansible applied host roles; still deliver app code via attach_deploy
            # so linked remotes clone (Ansible syncs the control-plane tree only).
            try:
                from app.services.git_urls import is_remote_cloneable_git_url

                deploy_instance = updated_instance
                if is_remote_cloneable_git_url(git_repo_url):
                    deploy_instance = updated_instance.model_copy(
                        update={"code_source": InstanceCodeSource.GITHUB}
                    )
                attach_result = _deploy_vm(
                    environment_id=environment_id,
                    name=environment_name,
                    image=image,
                    running_instance=deploy_instance,
                    settings=cfg,
                    cloud_provider=cloud_provider,
                    credentials=credentials,
                    workspace_root=root,
                    git_repo_url=git_repo_url,
                    git_branch=git_branch or "main",
                )
            except AttachDeployError as exc:
                if public_ip or preview_url:
                    logger.warning(
                        "scaffold_vm_app_delivery_deferred",
                        environment_id=environment_id,
                        host=public_ip,
                        detail=str(exc)[-800:],
                    )
                    notice = (
                        f"Scaffold deploy: IaC {apply.status}, Ansible applied; "
                        f"app delivery deferred: {exc}"
                    )
                else:
                    raise ScaffoldCloudDeployError(
                        f"IaC/Ansible ok but app delivery failed: {exc}"
                    ) from exc
            else:
                if attach_result.preview_url:
                    preview_url = attach_result.preview_url
                if attach_result.running_instance is not None:
                    updated_instance = attach_result.running_instance
                notice = (
                    f"Scaffold deploy: IaC {apply.status}, Ansible applied; "
                    f"app delivered"
                )

    if not preview_url and public_ip:
        preview_url = f"http://{public_ip}:{listen_port}"

    needs_preview = (
        running_instance.kind == RunningInstanceKind.VM
        and not (running_instance.preview_url_override or "").strip()
    )
    if needs_preview and not preview_url:
        raise ScaffoldCloudDeployError(
            "IaC applied but no public_ip/preview_url was exported. "
            "Enable compute_instance (GCP) or ec2 (AWS) in the workspace and retry."
        )

    return ProvisionedResources(
        namespace=namespace,
        preview_url=preview_url,
        created_workload=True,
        simulated=False,
        image=image,
        notice=notice,
        preview_endpoints=(
            [
                {
                    "name": updated_instance.service_name or "app",
                    "url": preview_url,
                    "port": listen_port,
                }
            ]
            if preview_url
            else []
        ),
        running_instance=updated_instance.model_copy(
            update={"host": public_ip or updated_instance.host}
        )
        if updated_instance
        else None,
    )
