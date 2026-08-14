"""Tests for AWS VPC listing / reuse helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import aws_client
from app.services.cloud_networks import _normalize_aws_region


def test_normalize_aws_region_rejects_gcp_style() -> None:
    assert _normalize_aws_region("us-central1") == "us-east-1"
    assert _normalize_aws_region("europe-west1") == "us-east-1"
    assert _normalize_aws_region("eu-central-1") == "eu-central-1"
    assert _normalize_aws_region("") == "us-east-1"


def test_list_vpcs_maps_name_and_default() -> None:
    ec2 = MagicMock()
    ec2.describe_vpcs.return_value = {
        "Vpcs": [
            {
                "VpcId": "vpc-aaa",
                "CidrBlock": "10.0.0.0/16",
                "IsDefault": False,
                "Tags": [{"Key": "Name", "Value": "app-vpc"}],
            },
            {
                "VpcId": "vpc-bbb",
                "CidrBlock": "172.31.0.0/16",
                "IsDefault": True,
                "Tags": [],
            },
        ]
    }
    with patch.object(aws_client, "_client", return_value=ec2):
        rows = aws_client.list_vpcs(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="eu-central-1",
        )
    assert rows[0]["id"] == "vpc-bbb"
    assert rows[0]["is_default"] is True
    assert rows[1]["name"] == "app-vpc"


def test_ensure_preview_network_uses_existing_vpc_id() -> None:
    with patch.object(
        aws_client,
        "subnet_in_vpc",
        return_value=("vpc-xyz", "subnet-1"),
    ) as subnet_fn:
        result = aws_client.ensure_preview_network(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="eu-central-1",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            create_vpc=True,
            vpc_id="vpc-xyz",
        )
    assert result == ("vpc-xyz", "subnet-1")
    subnet_fn.assert_called_once()


def test_list_security_groups_filters_by_vpc() -> None:
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [
            {
                "GroupId": "sg-111",
                "GroupName": "web",
                "VpcId": "vpc-aaa",
                "Description": "Web tier",
            },
        ]
    }
    with patch.object(aws_client, "_client", return_value=ec2):
        rows = aws_client.list_security_groups(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="eu-central-1",
            vpc_id="vpc-aaa",
        )
    assert rows[0]["id"] == "sg-111"
    assert rows[0]["name"] == "web"
    ec2.describe_security_groups.assert_called_once()
    assert ec2.describe_security_groups.call_args.kwargs["Filters"] == [
        {"Name": "vpc-id", "Values": ["vpc-aaa"]},
    ]


def test_ensure_preview_security_group_uses_existing_id() -> None:
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-custom", "VpcId": "vpc-123"}],
    }
    with patch.object(aws_client, "_client", return_value=ec2):
        sg = aws_client.ensure_preview_security_group(
            env={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"},
            region="eu-central-1",
            listen_port=8080,
            vpc_id="vpc-123",
            existing_security_group_id="sg-custom",
        )
    assert sg == "sg-custom"
    ec2.authorize_security_group_ingress.assert_called()
