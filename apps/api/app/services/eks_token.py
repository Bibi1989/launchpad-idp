"""Emit an EKS client-auth token for kubeconfig exec (no aws CLI).

Used by kubeconfigs written by ``write_eks_kubeconfig``. Reads AWS credentials
from the process environment / shared credentials file set on the exec plugin.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launchpad EKS token helper")
    parser.add_argument("--cluster-name", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args(argv)

    env = {key: value for key, value in os.environ.items() if key.startswith("AWS_")}
    from app.services.aws_client import AwsClientError, eks_bearer_token

    try:
        token = eks_bearer_token(
            env=env,
            region=args.region.strip(),
            cluster_name=args.cluster_name.strip(),
        )
    except AwsClientError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # client.authentication.k8s.io ExecCredential
    expiry = (datetime.now(UTC) + timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "kind": "ExecCredential",
        "apiVersion": "client.authentication.k8s.io/v1beta1",
        "status": {"token": token, "expirationTimestamp": expiry},
    }
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
