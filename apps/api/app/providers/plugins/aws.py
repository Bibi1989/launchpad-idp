"""AWS EC2 VM provider (native boto3 + cloud-init).

Boots an EC2 instance that runs the app container via cloud-init user-data - the same
model as Hetzner/DigitalOcean, but on AWS. Uses ``boto3`` (already a dependency); no
Terraform, no Ansible, no aws CLI subprocess. Idempotent + rollback-safe: a security
group created for the environment is torn down if instance launch fails.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger

from ..base import (
    CloudProviderAdapter,
    ComputeTier,
    CredentialError,
    CredentialField,
    DeploymentStatus,
    ProviderError,
    ProvisionResult,
    ProvisionSpec,
    RegionOption,
    RuntimeTarget,
    StatusResult,
    rollback_on_error,
)
from ..cloud_init import render_cloud_init

logger = get_logger(__name__)

# Canonical's public SSM parameter for the latest Ubuntu 22.04 LTS AMI in each region.
_UBUNTU_SSM = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"

_STATE_MAP = {
    "pending": DeploymentStatus.PROVISIONING,
    "running": DeploymentStatus.RUNNING,
    "stopping": DeploymentStatus.DEGRADED,
    "stopped": DeploymentStatus.DEGRADED,
    "shutting-down": DeploymentStatus.DESTROYED,
    "terminated": DeploymentStatus.DESTROYED,
}


class AWSProvider(CloudProviderAdapter):
    id = "aws"
    label = "Amazon Web Services (EC2)"
    runtime_targets = (RuntimeTarget.VM, RuntimeTarget.DOCKER_HOST)
    docs_url = "https://docs.aws.amazon.com/ec2/"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(name="aws_access_key_id", label="Access Key ID", secret=True),
            CredentialField(name="aws_secret_access_key", label="Secret Access Key", secret=True),
            CredentialField(name="aws_session_token", label="Session Token", secret=True, required=False,
                            help="Only for temporary STS credentials."),
            CredentialField(name="aws_region", label="Region", secret=False, required=False,
                            placeholder="us-east-1"),
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return [
            RegionOption(value="us-east-1", label="US East (N. Virginia)"),
            RegionOption(value="us-east-2", label="US East (Ohio)"),
            RegionOption(value="us-west-2", label="US West (Oregon)"),
            RegionOption(value="eu-west-1", label="EU (Ireland)"),
            RegionOption(value="eu-central-1", label="EU (Frankfurt)"),
            RegionOption(value="ap-southeast-1", label="Asia Pacific (Singapore)"),
        ]

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="t3.micro", label="t3.micro - 2 vCPU / 1 GB", vcpus=2, memory_mb=1024),
            ComputeTier(id="t3.small", label="t3.small - 2 vCPU / 2 GB", vcpus=2, memory_mb=2048),
            ComputeTier(id="t3.medium", label="t3.medium - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096),
            ComputeTier(id="t3.large", label="t3.large - 2 vCPU / 8 GB", vcpus=2, memory_mb=8192),
        ]

    # --- lifecycle ---
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        try:
            sts = self._client(credentials, "sts")
            sts.get_caller_identity()
            return True
        except Exception as exc:  # noqa: BLE001 - any auth failure is "invalid"
            logger.debug("aws_validate_failed", error=str(exc)[:200])
            return False

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        if not spec.image:
            raise CredentialError("AWS VM provider requires spec.image (a container image)")

        ec2 = self._client(credentials, "ec2", region=spec.region)
        ssm = self._client(credentials, "ssm", region=spec.region)
        instance_type = spec.tier or "t3.small"
        name = (spec.name or f"lp-{environment_id}")[:255]
        user_data = render_cloud_init(
            image=spec.image,
            app_port=spec.app_port,
            env_vars=spec.env_vars,
            ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
        )
        ami_id = ssm.get_parameter(Name=_UBUNTU_SSM)["Parameter"]["Value"]

        with rollback_on_error(self.label) as tracker:
            sg_id = self._ensure_security_group(ec2, environment_id, spec.app_port)
            tracker.track(sg_id, lambda gid=sg_id: self._delete_security_group(ec2, gid))

            run = ec2.run_instances(
                ImageId=ami_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                UserData=user_data,
                SecurityGroupIds=[sg_id],
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": name},
                            {"Key": "launchpad-environment", "Value": environment_id},
                        ],
                    }
                ],
            )
            instance = run["Instances"][0]
            instance_id = instance["InstanceId"]
            tracker.track(instance_id, lambda iid=instance_id: self._terminate(ec2, iid))

            ipv4 = instance.get("PublicIpAddress")
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.VM,
                resource_id=instance_id,
                resource_ids=[instance_id, sg_id],
                status=_STATE_MAP.get(instance.get("State", {}).get("Name", "pending"),
                                      DeploymentStatus.PROVISIONING),
                ip_address=ipv4,
                endpoints=[f"http://{ipv4}:{spec.app_port}"] if ipv4 else [],
                connection_meta={"ssh_user": "ubuntu", "ssh_port": 22, "app_port": spec.app_port,
                                 "security_group_id": sg_id, "region": spec.region},
                tags={"launchpad-environment": environment_id},
                metadata={"instance_type": instance_type, "ami_id": ami_id, "name": name},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        ec2 = self._client(credentials, "ec2", region=credentials.get("aws_region"))
        try:
            desc = ec2.describe_instances(InstanceIds=[resource_id])
        except Exception as exc:  # noqa: BLE001
            return StatusResult(status=DeploymentStatus.UNKNOWN, message=str(exc)[:200])
        reservations = desc.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            return StatusResult(status=DeploymentStatus.DESTROYED, message="instance not found")
        instance = reservations[0]["Instances"][0]
        ipv4 = instance.get("PublicIpAddress")
        state = instance.get("State", {}).get("Name", "unknown")
        return StatusResult(
            status=_STATE_MAP.get(state, DeploymentStatus.UNKNOWN),
            ip_address=ipv4,
            endpoints=[f"http://{ipv4}"] if ipv4 else [],
            raw={"state": state},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        ec2 = self._client(credentials, "ec2", region=credentials.get("aws_region"))
        self._terminate(ec2, resource_id)

    # --- helpers ---
    def _client(self, credentials: Mapping[str, str], service: str, *, region: str | None = None) -> Any:
        import boto3

        key = str(credentials.get("aws_access_key_id") or "").strip()
        secret = str(credentials.get("aws_secret_access_key") or "").strip()
        if not key or not secret:
            raise CredentialError("AWS provider requires aws_access_key_id and aws_secret_access_key")
        resolved_region = (region or credentials.get("aws_region") or "us-east-1").strip() or "us-east-1"
        return boto3.client(
            service,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            aws_session_token=(str(credentials.get("aws_session_token") or "").strip() or None),
            region_name=resolved_region,
        )

    def _ensure_security_group(self, ec2: Any, environment_id: str, app_port: int) -> str:
        group_name = f"launchpad-{environment_id}"[:255]
        try:
            created = ec2.create_security_group(
                GroupName=group_name,
                Description=f"Launchpad env {environment_id}",
            )
            sg_id = created["GroupId"]
        except Exception as exc:
            existing = ec2.describe_security_groups(
                Filters=[{"Name": "group-name", "Values": [group_name]}]
            ).get("SecurityGroups", [])
            if not existing:
                raise ProviderError(f"AWS: security group setup failed: {exc}") from exc
            return existing[0]["GroupId"]

        ports = sorted({22, int(app_port)})
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": p,
                    "ToPort": p,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }
                for p in ports
            ],
        )
        return sg_id

    def _delete_security_group(self, ec2: Any, sg_id: str) -> None:
        try:
            ec2.delete_security_group(GroupId=sg_id)
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.debug("aws_sg_delete_failed", sg=sg_id, error=str(exc)[:200])

    def _terminate(self, ec2: Any, instance_id: str) -> None:
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
        except Exception as exc:  # noqa: BLE001 - idempotent teardown
            logger.debug("aws_terminate_failed", instance=instance_id, error=str(exc)[:200])
