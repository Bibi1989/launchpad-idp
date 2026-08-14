"""Tests for AWS SDK client helpers."""

from __future__ import annotations

from app.services.aws_client import session_from_env


def test_session_from_env_uses_credentials_and_region() -> None:
    session = session_from_env(
        {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "token",
            "AWS_REGION": "eu-west-1",
        }
    )
    creds = session.get_credentials()
    assert creds is not None
    frozen = creds.get_frozen_credentials()
    assert frozen.access_key == "AKIATEST"
    assert frozen.secret_key == "secret"
    assert frozen.token == "token"
    assert session.region_name == "eu-west-1"


def test_session_from_env_uses_shared_credentials_file(tmp_path) -> None:
    creds = tmp_path / "creds"
    creds.write_text(
        "[default]\naws_access_key_id = AKIASHARED\naws_secret_access_key = sharedsecret\n",
        encoding="utf-8",
    )
    session = session_from_env(
        {
            "AWS_SHARED_CREDENTIALS_FILE": str(creds),
            "AWS_REGION": "eu-central-1",
        }
    )
    frozen = session.get_credentials().get_frozen_credentials()
    assert frozen.access_key == "AKIASHARED"
    assert frozen.secret_key == "sharedsecret"
    assert session.region_name == "eu-central-1"


def test_session_from_env_requires_credentials() -> None:
    from app.services.aws_client import AwsClientError

    try:
        session_from_env({"AWS_REGION": "us-east-1"})
        raise AssertionError("expected AwsClientError")
    except AwsClientError as exc:
        assert "credentials are missing" in str(exc).lower()


def test_run_ec2_instance_builds_vpc_network_interface() -> None:
    """SubnetId forces NetworkInterfaces with public IP (non-default VPC)."""
    from unittest.mock import MagicMock, patch

    from app.services import aws_client

    ec2 = MagicMock()
    ec2.run_instances.return_value = {"Instances": [{"InstanceId": "i-abc"}]}
    with (
        patch.object(aws_client, "_client", return_value=ec2),
        patch.object(aws_client, "resolve_al2023_ami_id", return_value="ami-123"),
    ):
        iid = aws_client.run_ec2_instance(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="us-east-1",
            instance_name="lp-demo",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            environment_name="demo",
            org_slug=None,
            user_data="#!/bin/bash\n",
            security_group_id="sg-1",
            subnet_id="subnet-1",
        )
    assert iid == "i-abc"
    kwargs = ec2.run_instances.call_args.kwargs
    assert kwargs["NetworkInterfaces"][0]["SubnetId"] == "subnet-1"
    assert kwargs["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is True
    assert kwargs["NetworkInterfaces"][0]["Groups"] == ["sg-1"]
    assert "SecurityGroupIds" not in kwargs


def test_ensure_eks_auto_roles_updates_cluster_trust_with_tag_session() -> None:
    """EKS Auto Mode requires sts:TagSession on the cluster role trust policy."""
    import json
    from unittest.mock import MagicMock, patch

    from app.services import aws_client

    iam = MagicMock()

    def get_role(*, RoleName: str):
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{RoleName}"}}

    iam.get_role.side_effect = get_role

    with patch.object(aws_client, "_client", return_value=iam):
        cluster_arn, node_arn = aws_client.ensure_eks_auto_roles(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="eu-central-1",
        )

    assert cluster_arn.endswith("launchpad-eks-cluster-role")
    assert node_arn.endswith("launchpad-eks-node-role")
    # First update_assume_role_policy call is for the cluster role.
    cluster_update = iam.update_assume_role_policy.call_args_list[0]
    trust = json.loads(cluster_update.kwargs["PolicyDocument"])
    actions = trust["Statement"][0]["Action"]
    assert "sts:AssumeRole" in actions
    assert "sts:TagSession" in actions


def test_parse_k8s_minor_and_version_constant() -> None:
    from app.services.aws_client import _EKS_KUBERNETES_VERSION, _parse_k8s_minor

    assert _EKS_KUBERNETES_VERSION == "1.36"
    assert _parse_k8s_minor("1.31") == (1, 31)
    assert _parse_k8s_minor("1.36") == (1, 36)


def test_discover_multi_az_public_subnets_same_vpc() -> None:
    """Never mix public subnets from different VPCs for EKS CreateCluster."""
    from unittest.mock import MagicMock

    from app.services import aws_client

    ec2 = MagicMock()
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": "subnet-other-a",
                "VpcId": "vpc-other",
                "AvailabilityZone": "eu-central-1a",
                "MapPublicIpOnLaunch": True,
                "Tags": [{"Key": "Name", "Value": "lp-preview-subnet-x"}],
            },
            {
                "SubnetId": "subnet-eks-b",
                "VpcId": "vpc-eks",
                "AvailabilityZone": "eu-central-1b",
                "MapPublicIpOnLaunch": True,
                "Tags": [
                    {"Key": "Name", "Value": "launchpad-eks-previews-eu-central-1b"},
                    {"Key": "kubernetes.io/role/elb", "Value": "1"},
                ],
            },
            {
                "SubnetId": "subnet-eks-a",
                "VpcId": "vpc-eks",
                "AvailabilityZone": "eu-central-1a",
                "MapPublicIpOnLaunch": True,
                "Tags": [
                    {"Key": "Name", "Value": "launchpad-eks-previews-eu-central-1a"},
                    {"Key": "kubernetes.io/role/elb", "Value": "1"},
                ],
            },
        ]
    }
    ids = aws_client._discover_multi_az_public_subnets(ec2)
    assert set(ids) == {"subnet-eks-a", "subnet-eks-b"}

    from unittest.mock import MagicMock, patch

    from app.services import aws_client

    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-xyz"}
    with patch.object(aws_client, "_client", return_value=ec2):
        sg = aws_client.ensure_preview_security_group(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="eu-west-1",
            listen_port=8080,
            vpc_id="vpc-123",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
    assert sg == "sg-xyz"
    assert ec2.create_security_group.call_args.kwargs["VpcId"] == "vpc-123"
