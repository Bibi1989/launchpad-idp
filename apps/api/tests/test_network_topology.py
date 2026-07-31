"""Network topology presets for VPC modules."""

from __future__ import annotations

from app.schemas.cloud import AwsResources, GcpResources, NetworkTopology
from app.services.terraform_bundle import _vpc_aws, _vpc_gcp


def test_gcp_simple_topology_single_subnet() -> None:
    hcl = _vpc_gcp(
        GcpResources(
            project_id="demo-proj",
            vpc=True,
            subnets=True,
            network_topology=NetworkTopology.SIMPLE,
        )
    )
    assert 'google_compute_subnetwork" "subnet"' in hcl
    assert "google_compute_router_nat" not in hcl


def test_gcp_standard_topology_public_private_nat() -> None:
    hcl = _vpc_gcp(
        GcpResources(
            project_id="demo-proj",
            vpc=True,
            subnets=True,
            network_topology=NetworkTopology.STANDARD,
        )
    )
    assert 'google_compute_subnetwork" "public"' in hcl
    assert 'google_compute_subnetwork" "private"' in hcl
    assert "google_compute_router_nat" in hcl


def test_aws_standard_includes_nat_gateway() -> None:
    hcl = _vpc_aws(
        AwsResources(
            vpc=True,
            subnets=True,
            network_topology=NetworkTopology.STANDARD,
        )
    )
    assert "aws_nat_gateway" in hcl
    assert "aws_internet_gateway" in hcl
    assert 'aws_subnet" "private"' in hcl


def test_aws_simple_single_public_subnet() -> None:
    hcl = _vpc_aws(
        AwsResources(
            vpc=True,
            subnets=True,
            network_topology=NetworkTopology.SIMPLE,
        )
    )
    assert "aws_internet_gateway" in hcl
    assert "aws_nat_gateway" not in hcl
    assert 'aws_subnet" "private"' not in hcl
