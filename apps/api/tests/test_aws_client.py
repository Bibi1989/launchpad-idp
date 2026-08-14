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


def test_ensure_preview_security_group_requires_vpc_id() -> None:
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
