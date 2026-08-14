"""AWS SDK helpers for EC2 / ECR (no aws CLI required on the worker host)."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from app.core.logging import get_logger, sanitize_log_message

if TYPE_CHECKING:
    import boto3

logger = get_logger(__name__)

_AL2023_AMI_SSM = (
    "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
)
_PREVIEW_SG_NAME = "lp-preview-sg"


class AwsClientError(RuntimeError):
    """AWS SDK operation failed."""


def session_from_env(env: dict[str, str], *, region: str | None = None) -> boto3.Session:
    """Build a boto3 session from Launchpad-materialized env (never ambient host ADC)."""
    import boto3

    access_key = (env.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = (env.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    session_token = (env.get("AWS_SESSION_TOKEN") or "").strip() or None
    region_name = (region or env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "").strip()

    if access_key and secret_key:
        kwargs: dict[str, str] = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        if session_token:
            kwargs["aws_session_token"] = session_token
        if region_name:
            kwargs["region_name"] = region_name
        return boto3.Session(**kwargs)

    # Resolve web-identity (role ARN + OIDC JWT) into temporary keys for the SDK.
    role_arn = (env.get("AWS_ROLE_ARN") or "").strip()
    token_file = (env.get("AWS_WEB_IDENTITY_TOKEN_FILE") or "").strip()
    if role_arn and token_file:
        if not _looks_like_role_arn(role_arn):
            raise AwsClientError(
                f"AWS role ARN is invalid ({role_arn[:48]}). "
                "Paste access keys in Settings, or Connect AWS SSO, instead of keyless OIDC "
                "unless the role ARN is a full IAM role ARN trusted by Launchpad OIDC."
            )
        keys = _assume_web_identity(
            env=env,
            role_arn=role_arn,
            token_file=token_file,
            region=region_name or "us-east-1",
        )
        kwargs = {
            "aws_access_key_id": keys["access_key_id"],
            "aws_secret_access_key": keys["secret_access_key"],
            "aws_session_token": keys["session_token"],
        }
        if region_name:
            kwargs["region_name"] = region_name
        return boto3.Session(**kwargs)

    raise AwsClientError(
        "AWS credentials are missing for this deploy. Paste access keys in Settings "
        "(preferred), or Connect AWS SSO with an account id and role name, then retry."
    )


def _looks_like_role_arn(value: str) -> bool:
    import re

    return bool(
        re.match(
            r"^arn:aws(?:-cn|-us-gov)?:iam::\d{12}:role/[\w+=,.@\-_/]+$",
            (value or "").strip(),
        )
    )


def _assume_web_identity(
    *,
    env: dict[str, str],
    role_arn: str,
    token_file: str,
    region: str,
) -> dict[str, str]:
    from pathlib import Path

    import boto3

    path = Path(token_file)
    if not path.is_file():
        raise AwsClientError(f"AWS web identity token file is missing ({token_file})")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise AwsClientError("AWS web identity token file is empty")
    session_name = (env.get("AWS_ROLE_SESSION_NAME") or "launchpad").strip() or "launchpad"
    # Unsigned STS client - web identity does not need prior keys.
    sts = boto3.client("sts", region_name=region)
    try:
        resp = sts.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName=session_name[:64],
            WebIdentityToken=token,
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "assume role with web identity") from exc
    creds = resp.get("Credentials") or {}
    access_key = str(creds.get("AccessKeyId") or "").strip()
    secret_key = str(creds.get("SecretAccessKey") or "").strip()
    session_token = str(creds.get("SessionToken") or "").strip()
    if not (access_key and secret_key and session_token):
        raise AwsClientError("STS web identity assume returned empty credentials")
    return {
        "access_key_id": access_key,
        "secret_access_key": secret_key,
        "session_token": session_token,
    }


def _client(env: dict[str, str], service: str, *, region: str) -> Any:
    return session_from_env(env, region=region).client(service, region_name=region)


def _wrap_aws_error(exc: Exception, action: str) -> AwsClientError:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        msg = exc.response.get("Error", {}).get("Message", str(exc))
        detail = sanitize_log_message(f"{code}: {msg}"[:500])
        return AwsClientError(f"AWS {action} failed: {detail}")
    if isinstance(exc, BotoCoreError):
        return AwsClientError(f"AWS {action} failed: {sanitize_log_message(str(exc)[:500])}")
    return AwsClientError(f"AWS {action} failed: {sanitize_log_message(str(exc)[:500])}")


def resolve_al2023_ami_id(*, env: dict[str, str], region: str) -> str:
    try:
        ssm = _client(env, "ssm", region=region)
        resp = ssm.get_parameter(Name=_AL2023_AMI_SSM)
        ami = str(resp.get("Parameter", {}).get("Value") or "").strip()
        if not ami:
            raise AwsClientError("SSM AMI parameter returned empty value")
        return ami
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "resolve AMI") from exc


def list_vpcs(*, env: dict[str, str], region: str) -> list[dict[str, Any]]:
    """Return VPCs in the region as ``{id, name, cidr, is_default}``."""
    try:
        ec2 = _client(env, "ec2", region=region)
        resp = ec2.describe_vpcs()
        rows: list[dict[str, Any]] = []
        for vpc in resp.get("Vpcs") or []:
            vpc_id = str(vpc.get("VpcId") or "").strip()
            if not vpc_id:
                continue
            name = vpc_id
            for tag in vpc.get("Tags") or []:
                if isinstance(tag, dict) and tag.get("Key") == "Name":
                    label = str(tag.get("Value") or "").strip()
                    if label:
                        name = label
                    break
            rows.append(
                {
                    "id": vpc_id,
                    "name": name,
                    "cidr": str(vpc.get("CidrBlock") or "").strip() or None,
                    "is_default": bool(vpc.get("IsDefault")),
                }
            )
        rows.sort(key=lambda r: (not r["is_default"], str(r["name"]).lower()))
        return rows
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "list VPCs") from exc


def list_security_groups(
    *,
    env: dict[str, str],
    region: str,
    vpc_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return security groups as ``{id, name, vpc_id, description}``."""
    try:
        ec2 = _client(env, "ec2", region=region)
        filters: list[dict[str, Any]] = []
        vpc = (vpc_id or "").strip()
        if vpc:
            filters.append({"Name": "vpc-id", "Values": [vpc]})
        kwargs: dict[str, Any] = {}
        if filters:
            kwargs["Filters"] = filters
        resp = ec2.describe_security_groups(**kwargs)
        rows: list[dict[str, Any]] = []
        for sg in resp.get("SecurityGroups") or []:
            sg_id = str(sg.get("GroupId") or "").strip()
            if not sg_id:
                continue
            name = str(sg.get("GroupName") or sg_id).strip() or sg_id
            rows.append(
                {
                    "id": sg_id,
                    "name": name,
                    "vpc_id": str(sg.get("VpcId") or "").strip() or None,
                    "description": str(sg.get("Description") or "").strip() or None,
                }
            )
        rows.sort(key=lambda r: str(r["name"]).lower())
        return rows
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "list security groups") from exc


def _authorize_preview_sg_ingress(
    ec2: Any,
    *,
    sg_id: str,
    listen_port: int,
) -> None:
    for from_port, to_port in ((22, 22), (listen_port, listen_port)):
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": from_port,
                        "ToPort": to_port,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    },
                ],
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"InvalidPermission.Duplicate"}:
                logger.warning(
                    "aws_sg_ingress_skipped",
                    group_id=sg_id,
                    code=code,
                    port=from_port,
                )


def subnet_in_vpc(
    *,
    env: dict[str, str],
    region: str,
    vpc_id: str,
) -> tuple[str, str]:
    """Pick a public (or any) subnet inside an existing VPC."""
    vpc = (vpc_id or "").strip()
    if not vpc:
        raise AwsClientError("VPC id is required")
    try:
        ec2 = _client(env, "ec2", region=region)
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}]).get(
            "Subnets"
        ) or []
        if not subnets:
            raise AwsClientError(
                f"VPC {vpc} has no subnets. Create a public subnet or choose Create new VPC."
            )
        public = [s for s in subnets if s.get("MapPublicIpOnLaunch")]
        chosen = (public or subnets)[0]
        subnet_id = str(chosen.get("SubnetId") or "").strip()
        if not subnet_id:
            raise AwsClientError(f"VPC {vpc} subnet list returned an empty subnet id")
        return vpc, subnet_id
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "resolve subnet in VPC") from exc


def ensure_preview_network(
    *,
    env: dict[str, str],
    region: str,
    environment_id: str,
    create_vpc: bool = True,
    vpc_id: str | None = None,
) -> tuple[str, str]:
    """Return (vpc_id, subnet_id) for preview EC2.

    When ``vpc_id`` is set, reuse that VPC (pick a public subnet). When create_vpc
    is true, create a tagged preview VPC+subnet if one for this environment does
    not already exist. Otherwise reuse a default VPC public subnet, or any public
    subnet, or create a preview VPC.
    """
    try:
        existing_id = (vpc_id or "").strip()
        if existing_id:
            return subnet_in_vpc(env=env, region=region, vpc_id=existing_id)

        ec2 = _client(env, "ec2", region=region)
        existing = _find_tagged_preview_network(ec2, environment_id=environment_id)
        if existing:
            return existing

        if not create_vpc:
            discovered = _discover_public_subnet(ec2)
            if discovered:
                return discovered

        return _create_preview_vpc_subnet(
            ec2,
            region=region,
            environment_id=environment_id,
        )
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "resolve VPC/subnet") from exc


def _find_tagged_preview_network(ec2: Any, *, environment_id: str) -> tuple[str, str] | None:
    vpcs = ec2.describe_vpcs(
        Filters=[
            {"Name": "tag:launchpad-environment-id", "Values": [environment_id]},
            {"Name": "tag:launchpad-managed", "Values": ["true"]},
        ],
    ).get("Vpcs") or []
    if not vpcs:
        return None
    vpc_id = str(vpcs[0].get("VpcId") or "").strip()
    if not vpc_id:
        return None
    subnets = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:launchpad-environment-id", "Values": [environment_id]},
        ],
    ).get("Subnets") or []
    if not subnets:
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
            "Subnets"
        ) or []
    if not subnets:
        return None
    subnet_id = str(subnets[0].get("SubnetId") or "").strip()
    return (vpc_id, subnet_id) if subnet_id else None


def _discover_public_subnet(ec2: Any) -> tuple[str, str] | None:
    """Prefer default-VPC public subnet, else any subnet with MapPublicIpOnLaunch."""
    vpcs = ec2.describe_vpcs().get("Vpcs") or []
    ordered: list[dict[str, Any]] = []
    for vpc in vpcs:
        if vpc.get("IsDefault"):
            ordered.insert(0, vpc)
        else:
            ordered.append(vpc)
    for vpc in ordered:
        vpc_id = str(vpc.get("VpcId") or "").strip()
        if not vpc_id:
            continue
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
            "Subnets"
        ) or []
        public = [s for s in subnets if s.get("MapPublicIpOnLaunch")]
        candidates = public or subnets
        if not candidates:
            continue
        subnet_id = str(candidates[0].get("SubnetId") or "").strip()
        if subnet_id:
            return vpc_id, subnet_id
    return None


def _create_preview_vpc_subnet(
    ec2: Any,
    *,
    region: str,
    environment_id: str,
) -> tuple[str, str]:
    short = environment_id.replace("-", "")[:12]
    base_tags = [
        {"Key": "launchpad-preview", "Value": "true"},
        {"Key": "launchpad-environment-id", "Value": environment_id},
        {"Key": "launchpad-managed", "Value": "true"},
    ]

    def _tags(name: str) -> list[dict[str, str]]:
        return [*base_tags, {"Key": "Name", "Value": name}]

    vpc = ec2.create_vpc(
        CidrBlock="10.42.0.0/16",
        TagSpecifications=[
            {"ResourceType": "vpc", "Tags": _tags(f"lp-preview-{short}")},
        ],
    )
    vpc_id = str(vpc.get("Vpc", {}).get("VpcId") or "").strip()
    if not vpc_id:
        raise AwsClientError("create_vpc returned empty VpcId")
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

    azs = ec2.describe_availability_zones(
        Filters=[
            {"Name": "region-name", "Values": [region]},
            {"Name": "state", "Values": ["available"]},
        ],
    ).get("AvailabilityZones") or []
    az = str((azs[0].get("ZoneName") if azs else "") or "").strip() or None

    subnet_params: dict[str, Any] = {
        "VpcId": vpc_id,
        "CidrBlock": "10.42.0.0/24",
        "TagSpecifications": [
            {
                "ResourceType": "subnet",
                "Tags": _tags(f"lp-preview-subnet-{short}"),
            }
        ],
    }
    if az:
        subnet_params["AvailabilityZone"] = az
    subnet = ec2.create_subnet(**subnet_params)
    subnet_id = str(subnet.get("Subnet", {}).get("SubnetId") or "").strip()
    if not subnet_id:
        raise AwsClientError("create_subnet returned empty SubnetId")
    ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={"Value": True})

    igw = ec2.create_internet_gateway(
        TagSpecifications=[
            {
                "ResourceType": "internet-gateway",
                "Tags": _tags(f"lp-preview-igw-{short}"),
            }
        ],
    )
    igw_id = str(igw.get("InternetGateway", {}).get("InternetGatewayId") or "").strip()
    if not igw_id:
        raise AwsClientError("create_internet_gateway returned empty id")
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

    routes = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
        "RouteTables"
    ) or []
    rtb_id = ""
    for table in routes:
        associations = table.get("Associations") or []
        if any(a.get("Main") for a in associations):
            rtb_id = str(table.get("RouteTableId") or "").strip()
            break
    if not rtb_id and routes:
        rtb_id = str(routes[0].get("RouteTableId") or "").strip()
    if rtb_id:
        try:
            ec2.create_route(
                RouteTableId=rtb_id,
                DestinationCidrBlock="0.0.0.0/0",
                GatewayId=igw_id,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"RouteAlreadyExists"}:
                raise

    logger.info(
        "aws_preview_vpc_created",
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        environment_id=environment_id,
        region=region,
    )
    return vpc_id, subnet_id


def _use_existing_preview_security_group(
    *,
    env: dict[str, str],
    region: str,
    listen_port: int,
    vpc_id: str,
    security_group_id: str,
) -> str | None:
    """Validate a user-selected SG and ensure SSH + app port ingress."""
    sg_id = (security_group_id or "").strip()
    if not sg_id:
        return None
    try:
        ec2 = _client(env, "ec2", region=region)
        described = ec2.describe_security_groups(GroupIds=[sg_id])
        groups = described.get("SecurityGroups") or []
        if not groups:
            raise AwsClientError(f"Security group {sg_id} was not found in {region}")
        group_vpc = str(groups[0].get("VpcId") or "").strip()
        expected_vpc = (vpc_id or "").strip()
        if expected_vpc and group_vpc and group_vpc != expected_vpc:
            raise AwsClientError(
                f"Security group {sg_id} belongs to {group_vpc}, not VPC {expected_vpc}"
            )
        _authorize_preview_sg_ingress(ec2, sg_id=sg_id, listen_port=listen_port)
        return sg_id
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "use existing security group") from exc


def ensure_preview_security_group(
    *,
    env: dict[str, str],
    region: str,
    listen_port: int,
    vpc_id: str,
    environment_id: str | None = None,
    existing_security_group_id: str | None = None,
) -> str | None:
    """Create or reuse lp-preview-sg in vpc_id; allow inbound on listen_port + SSH."""
    existing = (existing_security_group_id or "").strip() or None
    if existing:
        try:
            return _use_existing_preview_security_group(
                env=env,
                region=region,
                listen_port=listen_port,
                vpc_id=vpc_id,
                security_group_id=existing,
            )
        except AwsClientError as exc:
            logger.warning(
                "aws_existing_sg_failed",
                security_group_id=existing,
                error=sanitize_log_message(str(exc)[:200]),
            )
            return None
    try:
        ec2 = _client(env, "ec2", region=region)
        short = (environment_id or "preview").replace("-", "")[:12]
        group_name = f"lp-preview-sg-{short}" if environment_id else _PREVIEW_SG_NAME
        sg_id: str | None = None

        described = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [group_name, _PREVIEW_SG_NAME]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ],
        )
        groups = described.get("SecurityGroups") or []
        if groups:
            sg_id = str(groups[0].get("GroupId") or "").strip() or None

        if not sg_id:
            try:
                created = ec2.create_security_group(
                    GroupName=group_name,
                    Description="Launchpad preview instances",
                    VpcId=vpc_id,
                    TagSpecifications=[
                        {
                            "ResourceType": "security-group",
                            "Tags": [
                                {"Key": "Name", "Value": group_name},
                                {"Key": "launchpad-preview", "Value": "true"},
                                {"Key": "launchpad-managed", "Value": "true"},
                                *(
                                    [{"Key": "launchpad-environment-id", "Value": environment_id}]
                                    if environment_id
                                    else []
                                ),
                            ],
                        }
                    ],
                )
                sg_id = str(created.get("GroupId") or "").strip() or None
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code not in {"InvalidGroup.Duplicate"}:
                    raise
                described = ec2.describe_security_groups(
                    Filters=[
                        {"Name": "group-name", "Values": [group_name]},
                        {"Name": "vpc-id", "Values": [vpc_id]},
                    ],
                )
                groups = described.get("SecurityGroups") or []
                if groups:
                    sg_id = str(groups[0].get("GroupId") or "").strip() or None

        if not sg_id:
            return None

        _authorize_preview_sg_ingress(ec2, sg_id=sg_id, listen_port=listen_port)
        return sg_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("aws_sg_setup_failed", error=sanitize_log_message(str(exc)[:200]))
        return None


def run_ec2_instance(
    *,
    env: dict[str, str],
    region: str,
    instance_name: str,
    environment_id: str,
    environment_name: str,
    org_slug: str | None,
    user_data: str,
    security_group_id: str | None,
    subnet_id: str | None = None,
) -> str:
    try:
        ec2 = _client(env, "ec2", region=region)
        ami_id = resolve_al2023_ami_id(env=env, region=region)
        tags = [
            {"Key": "Name", "Value": instance_name},
            {"Key": "launchpad-preview", "Value": "true"},
            {"Key": "launchpad-environment-id", "Value": environment_id},
            {"Key": "launchpad-env-name", "Value": (environment_name or "preview")[:256]},
            {"Key": "launchpad-org-slug", "Value": (org_slug or "none")[:256]},
            {"Key": "launchpad-managed", "Value": "true"},
        ]
        params: dict[str, Any] = {
            "ImageId": ami_id,
            "InstanceType": "t3.small",
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": base64.b64encode(user_data.encode("utf-8")).decode("ascii"),
            "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}],
        }
        if subnet_id:
            # VPC launch: place NIC in subnet and request a public IP.
            nic: dict[str, Any] = {
                "DeviceIndex": 0,
                "SubnetId": subnet_id,
                "AssociatePublicIpAddress": True,
            }
            if security_group_id:
                nic["Groups"] = [security_group_id]
            params["NetworkInterfaces"] = [nic]
        elif security_group_id:
            params["SecurityGroupIds"] = [security_group_id]
        resp = ec2.run_instances(**params)
        instances = resp.get("Instances") or []
        if not instances:
            raise AwsClientError("run_instances returned no instances")
        instance_id = str(instances[0].get("InstanceId") or "").strip()
        if not instance_id:
            raise AwsClientError("run_instances returned empty instance id")
        return instance_id
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "EC2 create") from exc


def wait_ec2_instance_running(
    *,
    env: dict[str, str],
    region: str,
    instance_id: str,
    timeout_seconds: int = 600,
) -> None:
    try:
        ec2 = _client(env, "ec2", region=region)
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": max(1, timeout_seconds // 15)},
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "EC2 wait running") from exc


def ec2_instance_availability_zone(
    *,
    env: dict[str, str],
    region: str,
    instance_id: str,
) -> str:
    try:
        ec2 = _client(env, "ec2", region=region)
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        for reservation in resp.get("Reservations") or []:
            for instance in reservation.get("Instances") or []:
                az = str((instance.get("Placement") or {}).get("AvailabilityZone") or "").strip()
                if az:
                    return az
        raise AwsClientError(f"EC2 instance {instance_id} has no availability zone")
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "describe instance AZ") from exc


def send_ec2_instance_connect_key(
    *,
    env: dict[str, str],
    region: str,
    instance_id: str,
    availability_zone: str,
    os_user: str,
    public_key: str,
) -> None:
    """Push a 60-second SSH public key via EC2 Instance Connect (reuse / bootstrap)."""
    key = (public_key or "").strip()
    if not key:
        raise AwsClientError("SSH public key is empty")
    try:
        client = _client(env, "ec2-instance-connect", region=region)
        client.send_ssh_public_key(
            InstanceId=instance_id,
            InstanceOSUser=(os_user or "ec2-user").strip() or "ec2-user",
            SSHPublicKey=key,
            AvailabilityZone=availability_zone,
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "EC2 Instance Connect") from exc


def ec2_instance_public_ip(
    *,
    env: dict[str, str],
    region: str,
    instance_id: str,
) -> str:
    try:
        ec2 = _client(env, "ec2", region=region)
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = resp.get("Reservations") or []
        for reservation in reservations:
            for instance in reservation.get("Instances") or []:
                host = str(instance.get("PublicIpAddress") or "").strip()
                if host:
                    return host
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "aws_describe_instance_ip_failed",
            instance_id=instance_id,
            error=sanitize_log_message(str(exc)[:200]),
        )
        return ""


def list_ec2_instance_ids(
    *,
    env: dict[str, str],
    region: str,
    environment_id: str,
    name_candidates: list[str],
) -> list[str]:
    try:
        ec2 = _client(env, "ec2", region=region)
        instance_ids: list[str] = []
        by_tag = ec2.describe_instances(
            Filters=[
                {"Name": "tag:launchpad-environment-id", "Values": [environment_id]},
                {
                    "Name": "instance-state-name",
                    "Values": ["running", "pending", "stopping", "stopped"],
                },
            ],
        )
        instance_ids.extend(_instance_ids_from_response(by_tag))

        for candidate in name_candidates:
            if candidate.startswith("i-"):
                instance_ids.append(candidate)
                continue
            by_name = ec2.describe_instances(
                Filters=[
                    {"Name": "tag:Name", "Values": [candidate]},
                    {
                        "Name": "instance-state-name",
                        "Values": ["running", "pending", "stopping", "stopped"],
                    },
                ],
            )
            instance_ids.extend(_instance_ids_from_response(by_name))

        seen: set[str] = set()
        unique: list[str] = []
        for iid in instance_ids:
            token = iid.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            unique.append(token)
        return unique
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "aws_list_instances_failed",
            environment_id=environment_id,
            error=sanitize_log_message(str(exc)[:200]),
        )
        return []


def terminate_ec2_instances(
    *,
    env: dict[str, str],
    region: str,
    instance_ids: list[str],
) -> None:
    if not instance_ids:
        return
    try:
        ec2 = _client(env, "ec2", region=region)
        ec2.terminate_instances(InstanceIds=instance_ids)
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "EC2 terminate") from exc


def ensure_ecr_repository(
    *,
    env: dict[str, str],
    region: str,
    repo: str,
) -> str | None:
    try:
        ecr = _client(env, "ecr", region=region)
        try:
            ecr.describe_repositories(repositoryNames=[repo])
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "RepositoryNotFoundException":
                ecr.create_repository(repositoryName=repo)
            else:
                raise
        sts = _client(env, "sts", region=region)
        account = str(sts.get_caller_identity().get("Account") or "").strip()
        if not account:
            return None
        return f"{account}.dkr.ecr.{region}.amazonaws.com/{repo}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("aws_ecr_repo_failed", error=sanitize_log_message(str(exc)[:200]))
        return None


def ecr_login_password(*, env: dict[str, str], region: str) -> str:
    try:
        ecr = _client(env, "ecr", region=region)
        resp = ecr.get_authorization_token()
        tokens = resp.get("authorizationData") or []
        if not tokens:
            return ""
        token = str(tokens[0].get("authorizationToken") or "")
        if not token:
            return ""
        decoded = base64.b64decode(token).decode("utf-8")
        _, password = decoded.split(":", 1)
        return password.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("aws_ecr_login_failed", error=sanitize_log_message(str(exc)[:200]))
        return ""


def delete_ecr_images(
    *,
    env: dict[str, str],
    region: str,
    repository: str,
    image_tags: list[str],
) -> int:
    """Delete tagged images from an ECR repository. Returns count deleted."""
    tags = [t.strip() for t in image_tags if t and t.strip()]
    if not tags:
        return 0
    try:
        ecr = _client(env, "ecr", region=region)
        resp = ecr.batch_delete_image(
            repositoryName=repository,
            imageIds=[{"imageTag": tag} for tag in tags],
        )
        deleted = resp.get("imageIds") or []
        failures = resp.get("failures") or []
        if failures:
            logger.warning(
                "aws_ecr_delete_partial",
                repository=repository,
                failures=sanitize_log_message(str(failures)[:300]),
            )
        return len(deleted)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "aws_ecr_delete_failed",
            repository=repository,
            error=sanitize_log_message(str(exc)[:200]),
        )
        return 0


def _instance_ids_from_response(resp: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for reservation in resp.get("Reservations") or []:
        for instance in reservation.get("Instances") or []:
            iid = str(instance.get("InstanceId") or "").strip()
            if iid:
                ids.append(iid)
    return ids


# --------------------------------------------------------------------------- #
# EKS (boto3 - no aws CLI / eksctl on the worker host)
# --------------------------------------------------------------------------- #

_EKS_CLUSTER_ROLE_NAME = "launchpad-eks-cluster-role"
_EKS_NODE_ROLE_NAME = "launchpad-eks-node-role"
_EKS_CLUSTER_POLICIES = (
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSComputePolicy",
    "arn:aws:iam::aws:policy/AmazonEKSBlockStoragePolicy",
    "arn:aws:iam::aws:policy/AmazonEKSLoadBalancingPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSNetworkingPolicy",
)
_EKS_NODE_POLICIES = (
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodeMinimalPolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
)


def sts_account_id(*, env: dict[str, str], region: str) -> str | None:
    try:
        sts = _client(env, "sts", region=region)
        resp = sts.get_caller_identity()
        account = str(resp.get("Account") or "").strip()
        return account or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("aws_sts_account_failed", error=sanitize_log_message(str(exc)[:200]))
        return None


def list_eks_cluster_names(*, env: dict[str, str], region: str) -> list[str]:
    try:
        eks = _client(env, "eks", region=region)
        names: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {}
            if token:
                kwargs["nextToken"] = token
            resp = eks.list_clusters(**kwargs)
            for name in resp.get("clusters") or []:
                if str(name).strip():
                    names.append(str(name).strip())
            token = resp.get("nextToken")
            if not token:
                break
        return names
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "list EKS clusters") from exc


def eks_cluster_status(*, env: dict[str, str], region: str, name: str) -> str | None:
    try:
        eks = _client(env, "eks", region=region)
        resp = eks.describe_cluster(name=name)
        status = str((resp.get("cluster") or {}).get("status") or "").strip().upper()
        return status or None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ResourceNotFoundException", "ResourceNotFound"}:
            return None
        raise _wrap_aws_error(exc, "describe EKS cluster") from exc
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "describe EKS cluster") from exc


def wait_eks_cluster_active(
    *,
    env: dict[str, str],
    region: str,
    name: str,
    timeout_seconds: float = 1200.0,
) -> None:
    import time

    deadline = time.time() + max(timeout_seconds, 30.0)
    last = "UNKNOWN"
    while time.time() < deadline:
        last = eks_cluster_status(env=env, region=region, name=name) or "UNKNOWN"
        if last == "ACTIVE":
            return
        if last in {"FAILED", "DELETING"}:
            raise AwsClientError(
                f"EKS cluster '{name}' entered {last} while waiting to become ACTIVE."
            )
        time.sleep(15.0)
    raise AwsClientError(
        f"Timed out waiting for EKS cluster '{name}' to become ACTIVE (last status={last})."
    )


def ensure_eks_preview_subnets(*, env: dict[str, str], region: str) -> list[str]:
    """Return at least two subnet ids in different AZs for EKS (create VPC if needed)."""
    try:
        ec2 = _client(env, "ec2", region=region)
        discovered = _discover_multi_az_public_subnets(ec2)
        if discovered and len(discovered) >= 2:
            return discovered[:4]
        return _create_eks_preview_vpc(ec2, region=region)
    except AwsClientError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "resolve EKS subnets") from exc


def _discover_multi_az_public_subnets(ec2: Any) -> list[str]:
    subnets = ec2.describe_subnets().get("Subnets") or []
    public = [s for s in subnets if s.get("MapPublicIpOnLaunch")]
    by_az: dict[str, str] = {}
    for subnet in public or subnets:
        az = str(subnet.get("AvailabilityZone") or "").strip()
        sid = str(subnet.get("SubnetId") or "").strip()
        if az and sid and az not in by_az:
            by_az[az] = sid
        if len(by_az) >= 2:
            break
    return list(by_az.values())


def _create_eks_preview_vpc(ec2: Any, *, region: str) -> list[str]:
    """Create a small dual-AZ public VPC dedicated to Launchpad EKS previews."""
    azs = ec2.describe_availability_zones(
        Filters=[{"Name": "region-name", "Values": [region]}, {"Name": "state", "Values": ["available"]}]
    ).get("AvailabilityZones") or []
    zone_names = [str(z.get("ZoneName") or "").strip() for z in azs if z.get("ZoneName")]
    if len(zone_names) < 2:
        raise AwsClientError(
            f"Region {region} needs at least two availability zones to create an EKS cluster."
        )
    vpc = ec2.create_vpc(
        CidrBlock="10.50.0.0/16",
        TagSpecifications=[
            {"ResourceType": "vpc", "Tags": [{"Key": "Name", "Value": "launchpad-eks-previews"}]}
        ],
    )
    vpc_id = str(vpc.get("Vpc", {}).get("VpcId") or "").strip()
    if not vpc_id:
        raise AwsClientError("create_vpc returned empty VpcId for EKS")
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    igw = ec2.create_internet_gateway(
        TagSpecifications=[
            {
                "ResourceType": "internet-gateway",
                "Tags": [{"Key": "Name", "Value": "launchpad-eks-previews-igw"}],
            }
        ]
    )
    igw_id = str(igw.get("InternetGateway", {}).get("InternetGatewayId") or "").strip()
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    subnet_ids: list[str] = []
    cidrs = ("10.50.0.0/20", "10.50.16.0/20")
    for idx, az in enumerate(zone_names[:2]):
        subnet = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidrs[idx],
            AvailabilityZone=az,
            TagSpecifications=[
                {
                    "ResourceType": "subnet",
                    "Tags": [{"Key": "Name", "Value": f"launchpad-eks-previews-{az}"}],
                }
            ],
        )
        sid = str(subnet.get("Subnet", {}).get("SubnetId") or "").strip()
        if not sid:
            raise AwsClientError("create_subnet returned empty SubnetId for EKS")
        ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={"Value": True})
        # EKS / ELB discovery tags for public subnets.
        ec2.create_tags(
            Resources=[sid],
            Tags=[
                {"Key": "kubernetes.io/role/elb", "Value": "1"},
                {"Key": "kubernetes.io/cluster/launchpad-previews", "Value": "shared"},
            ],
        )
        subnet_ids.append(sid)
    routes = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
        "RouteTables"
    ) or []
    rtb_id = str((routes[0] if routes else {}).get("RouteTableId") or "").strip()
    if not rtb_id:
        raise AwsClientError("EKS VPC has no route table")
    ec2.create_route(RouteTableId=rtb_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    for sid in subnet_ids:
        ec2.associate_route_table(RouteTableId=rtb_id, SubnetId=sid)
    logger.info("aws_eks_preview_vpc_created", vpc_id=vpc_id, subnet_ids=subnet_ids, region=region)
    return subnet_ids


def _ensure_iam_role(
    *,
    env: dict[str, str],
    region: str,
    role_name: str,
    trust_service: str,
    policy_arns: tuple[str, ...],
) -> str:
    iam = _client(env, "iam", region=region)
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": trust_service},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    import json

    try:
        existing = iam.get_role(RoleName=role_name)
        role_arn = str((existing.get("Role") or {}).get("Arn") or "").strip()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"NoSuchEntity", "NoSuchEntityException"}:
            raise _wrap_aws_error(exc, f"get IAM role {role_name}") from exc
        created = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Launchpad EKS Auto Mode role",
            Tags=[{"Key": "ManagedBy", "Value": "launchpad"}],
        )
        role_arn = str((created.get("Role") or {}).get("Arn") or "").strip()
    if not role_arn:
        raise AwsClientError(f"IAM role {role_name} has an empty ARN")
    for policy in policy_arns:
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"EntityAlreadyExists", "LimitExceeded"}:
                # Already attached is fine; other errors should surface.
                msg = str(exc.response.get("Error", {}).get("Message") or "")
                if "already" not in msg.lower():
                    raise _wrap_aws_error(exc, f"attach {policy} to {role_name}") from exc
    return role_arn


def ensure_eks_auto_roles(*, env: dict[str, str], region: str) -> tuple[str, str]:
    """Return (cluster_role_arn, node_role_arn) for EKS Auto Mode."""
    cluster_arn = _ensure_iam_role(
        env=env,
        region=region,
        role_name=_EKS_CLUSTER_ROLE_NAME,
        trust_service="eks.amazonaws.com",
        policy_arns=_EKS_CLUSTER_POLICIES,
    )
    node_arn = _ensure_iam_role(
        env=env,
        region=region,
        role_name=_EKS_NODE_ROLE_NAME,
        trust_service="ec2.amazonaws.com",
        policy_arns=_EKS_NODE_POLICIES,
    )
    return cluster_arn, node_arn


def create_eks_auto_cluster(
    *,
    env: dict[str, str],
    region: str,
    name: str,
    subnet_ids: list[str],
    cluster_role_arn: str,
    node_role_arn: str,
) -> None:
    """Create an EKS Auto Mode cluster via the EKS API (no eksctl)."""
    import time

    if len(subnet_ids) < 2:
        raise AwsClientError("EKS requires at least two subnet ids in different AZs")
    eks = _client(env, "eks", region=region)
    logger.info("eks_auto_create_start", cluster=name, region=region)
    try:
        eks.create_cluster(
            name=name,
            version="1.31",
            roleArn=cluster_role_arn,
            resourcesVpcConfig={
                "subnetIds": subnet_ids,
                "endpointPublicAccess": True,
                "endpointPrivateAccess": True,
                "publicAccessCidrs": ["0.0.0.0/0"],
            },
            accessConfig={"authenticationMode": "API_AND_CONFIG_MAP"},
            computeConfig={
                "enabled": True,
                "nodePools": ["general-purpose", "system"],
                "nodeRoleArn": node_role_arn,
            },
            kubernetesNetworkConfig={"elasticLoadBalancing": {"enabled": True}},
            storageConfig={"blockStorage": {"enabled": True}},
            tags={"ManagedBy": "launchpad", "Name": name},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        msg = str(exc.response.get("Error", {}).get("Message") or "")
        if code in {"ResourceInUseException", "ResourceInUse"} or "already exists" in msg.lower():
            return
        raise _wrap_aws_error(exc, "create EKS Auto Mode cluster") from exc
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "create EKS Auto Mode cluster") from exc
    # IAM role propagation can race; wait briefly then poll ACTIVE.
    time.sleep(5.0)
    wait_eks_cluster_active(env=env, region=region, name=name, timeout_seconds=1500.0)


def eks_bearer_token(*, env: dict[str, str], region: str, cluster_name: str) -> str:
    """Build a short-lived EKS auth token (same format as ``aws eks get-token``)."""
    import base64
    from botocore.signers import RequestSigner

    session = session_from_env(env, region=region)
    credentials = session.get_credentials()
    if credentials is None:
        raise AwsClientError("AWS credentials are missing for EKS token generation")
    frozen = credentials.get_frozen_credentials()
    # STS regional endpoint signing for GetCallerIdentity (EKS auth protocol).
    sts = session.client("sts", region_name=region)
    signer = RequestSigner(
        sts.meta.service_model.service_id,
        region,
        "sts",
        "v4",
        credentials,
        session.events,
    )
    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }
    signed_url = signer.generate_presigned_url(
        params,
        region_name=region,
        expires_in=60,
        operation_name="",
    )
    # Token format: k8s-aws-v1. + base64url(signed_url) without padding.
    encoded = base64.urlsafe_b64encode(signed_url.encode("utf-8")).decode("utf-8").rstrip("=")
    _ = frozen  # ensure credentials resolved
    return f"k8s-aws-v1.{encoded}"


def write_eks_kubeconfig(
    *,
    env: dict[str, str],
    region: str,
    cluster_name: str,
    kubeconfig_path: str,
) -> str:
    """Write a kubeconfig that authenticates via Launchpad's Python EKS token helper."""
    import json
    import sys
    from pathlib import Path

    import yaml

    eks = _client(env, "eks", region=region)
    try:
        described = eks.describe_cluster(name=cluster_name)
    except Exception as exc:  # noqa: BLE001
        raise _wrap_aws_error(exc, "describe EKS cluster for kubeconfig") from exc
    cluster = described.get("cluster") or {}
    endpoint = str(cluster.get("endpoint") or "").strip()
    ca = str((cluster.get("certificateAuthority") or {}).get("data") or "").strip()
    arn = str(cluster.get("arn") or "").strip()
    if not endpoint or not ca:
        raise AwsClientError(f"EKS cluster '{cluster_name}' is missing endpoint or CA data")
    context_name = arn or f"arn:aws:eks:{region}:account:cluster/{cluster_name}"

    path = Path(kubeconfig_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    creds_path = path.with_suffix(path.suffix + ".awscreds")
    access = (env.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (env.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    token = (env.get("AWS_SESSION_TOKEN") or "").strip()
    if not (access and secret):
        raise AwsClientError(
            "AWS access keys are required to write an EKS kubeconfig. "
            "Save access keys in Settings (or Connect AWS SSO), then retry."
        )
    creds_lines = [
        "[default]",
        f"aws_access_key_id = {access}",
        f"aws_secret_access_key = {secret}",
    ]
    if token:
        creds_lines.append(f"aws_session_token = {token}")
    creds_path.write_text("\n".join(creds_lines) + "\n", encoding="utf-8")
    try:
        creds_path.chmod(0o600)
    except OSError:
        pass

    # exec plugin uses Launchpad's Python module so aws CLI is not required.
    api_root = str(Path(__file__).resolve().parents[2])
    exec_env = [
        {"name": "AWS_SHARED_CREDENTIALS_FILE", "value": str(creds_path)},
        {"name": "AWS_DEFAULT_REGION", "value": region},
        {"name": "AWS_REGION", "value": region},
        {"name": "AWS_CONFIG_FILE", "value": str(creds_path.with_suffix(".awsconfig"))},
        {"name": "PYTHONPATH", "value": api_root},
        {"name": "PYTHONUNBUFFERED", "value": "1"},
    ]
    creds_path.with_suffix(".awsconfig").write_text(
        f"[default]\nregion = {region}\n", encoding="utf-8"
    )

    config = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": context_name,
                "cluster": {
                    "server": endpoint,
                    "certificate-authority-data": ca,
                },
            }
        ],
        "contexts": [
            {
                "name": context_name,
                "context": {"cluster": context_name, "user": context_name},
            }
        ],
        "current-context": context_name,
        "users": [
            {
                "name": context_name,
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1beta1",
                        "command": sys.executable,
                        "args": [
                            "-m",
                            "app.services.eks_token",
                            "--cluster-name",
                            cluster_name,
                            "--region",
                            region,
                        ],
                        "env": exec_env,
                        "provideClusterInfo": False,
                    }
                },
            }
        ],
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    logger.info(
        "eks_kubeconfig_written",
        cluster=cluster_name,
        region=region,
        path=str(path),
        meta=json.dumps({"auth": "launchpad-eks-token"}),
    )
    return context_name
