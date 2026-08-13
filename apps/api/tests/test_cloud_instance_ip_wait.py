"""AWS EC2 public-IP wait: poll instead of failing on the first empty read."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from app.services import cloud_instance_compute as cic


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["aws"], 0, stdout=stdout, stderr="")


def test_public_ip_treats_none_as_empty() -> None:
    with patch.object(cic, "_run_cmd", return_value=_completed("None\n")):
        assert cic._aws_instance_public_ip(instance_id="i-1", region="us-east-1", env={}) == ""


def test_public_ip_returns_assigned_ip() -> None:
    with patch.object(cic, "_run_cmd", return_value=_completed("52.1.2.3\n")):
        assert cic._aws_instance_public_ip(instance_id="i-1", region="us-east-1", env={}) == "52.1.2.3"


def test_wait_polls_until_ip_appears() -> None:
    # First two reads: not-ready ("None" / empty); third: the IP is assigned.
    outputs = [_completed("None\n"), _completed("\n"), _completed("52.9.9.9\n")]
    with (
        patch.object(cic, "_run_cmd", side_effect=outputs),
        patch.object(cic.time, "sleep", return_value=None),
    ):
        host = cic._wait_aws_instance_ip(instance_id="i-1", region="us-east-1", env={}, attempts=5)
    assert host == "52.9.9.9"


def test_wait_gives_up_after_attempts() -> None:
    with (
        patch.object(cic, "_run_cmd", return_value=_completed("None\n")),
        patch.object(cic.time, "sleep", return_value=None),
    ):
        host = cic._wait_aws_instance_ip(instance_id="i-1", region="us-east-1", env={}, attempts=3)
    assert host == ""
