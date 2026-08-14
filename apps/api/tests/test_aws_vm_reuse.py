"""AWS EC2 reuse on provision retry."""

from __future__ import annotations

from unittest.mock import patch

from app.schemas.cloud import RunningInstanceConfig, RunningInstanceKind
from app.services.cloud_instance_compute import _reuse_aws_vm


def test_reuse_aws_vm_returns_existing_instance() -> None:
    running = RunningInstanceConfig(kind=RunningInstanceKind.VM, listen_port=8080)
    env = {"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S", "AWS_REGION": "eu-west-1"}

    with (
        patch(
            "app.services.aws_client.list_ec2_instance_ids",
            return_value=["i-existing"],
        ),
        patch(
            "app.services.cloud_instance_compute._wait_aws_instance_ip",
            return_value="203.0.113.10",
        ),
    ):
        reused = _reuse_aws_vm(
            running_instance=running,
            instance_name="lp-demo-abc",
            region="eu-west-1",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            env=env,
        )

    assert reused is not None
    assert reused.host == "203.0.113.10"
    assert reused.service_name == "i-existing"
    assert reused.ssh_user == "ec2-user"


def test_reuse_aws_vm_returns_none_when_missing() -> None:
    running = RunningInstanceConfig(kind=RunningInstanceKind.VM, listen_port=8080)
    env = {"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S", "AWS_REGION": "eu-west-1"}

    with patch(
        "app.services.aws_client.list_ec2_instance_ids",
        return_value=[],
    ):
        reused = _reuse_aws_vm(
            running_instance=running,
            instance_name="lp-demo-abc",
            region="eu-west-1",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            env=env,
        )

    assert reused is None
