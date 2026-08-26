"""Railway PaaS provider (native GraphQL API).

Deploys a container image (or git repo) as a Railway service - no VM, no cloud-init.
Uses only ``httpx`` against Railway's public GraphQL API. Idempotent + rollback-safe:
on partial failure the created project is torn down.

API reference: https://docs.railway.com/reference/public-api
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.core.logging import get_logger

from ..base import (
    CloudProviderAdapter,
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

logger = get_logger(__name__)

_API = "https://backboard.railway.com/graphql/v2"


class RailwayProvider(CloudProviderAdapter):
    id = "railway"
    label = "Railway"
    runtime_targets = (RuntimeTarget.PAAS,)
    docs_url = "https://docs.railway.com/reference/public-api"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                name="api_token",
                label="Account or Team Token",
                secret=True,
                required=True,
                help="Railway API token (Account Settings > Tokens).",
                placeholder="railway-api-token",
            )
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return [
            RegionOption(value="us-west1", label="US West (us-west1)"),
            RegionOption(value="us-east4", label="US East (us-east4)"),
            RegionOption(value="europe-west4", label="EU West (europe-west4)"),
            RegionOption(value="asia-southeast1", label="Asia SE (asia-southeast1)"),
        ]

    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        token = self._require(credentials, "api_token")
        try:
            data = self._gql(token, "query { me { id } }")
            return bool(data.get("me", {}).get("id"))
        except ProviderError:
            return False

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        token = self._require(credentials, "api_token")
        source_image = spec.image
        source_repo = spec.git_repo_url
        if not source_image and not source_repo:
            raise CredentialError("Railway provider requires spec.image or spec.git_repo_url")

        project_name = (spec.name or f"lp-{environment_id}")[:63]

        with rollback_on_error(self.label) as tracker:
            # 1. Project (rolled back on any later failure).
            project = self._gql(
                token,
                """
                mutation($input: ProjectCreateInput!) {
                  projectCreate(input: $input) { id name environments { edges { node { id } } } }
                }
                """,
                {"input": {"name": project_name}},
            )["projectCreate"]
            project_id = project["id"]
            tracker.track(project_id, lambda pid=project_id: self._delete_project(token, pid))

            rw_env_edges = project.get("environments", {}).get("edges", [])
            rw_environment_id = rw_env_edges[0]["node"]["id"] if rw_env_edges else None

            # 2. Service from image or repo.
            service_input: dict[str, Any] = {"projectId": project_id, "name": project_name}
            if source_image:
                service_input["source"] = {"image": source_image}
            else:
                service_input["source"] = {"repo": source_repo}
            service = self._gql(
                token,
                """
                mutation($input: ServiceCreateInput!) {
                  serviceCreate(input: $input) { id }
                }
                """,
                {"input": service_input},
            )["serviceCreate"]
            service_id = service["id"]

            # 3. Inject env vars (best-effort per variable).
            if spec.env_vars and rw_environment_id:
                self._upsert_variables(
                    token,
                    project_id=project_id,
                    environment_id=rw_environment_id,
                    service_id=service_id,
                    variables=spec.env_vars,
                )

            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.PAAS,
                resource_id=service_id,
                resource_ids=[project_id, service_id],
                status=DeploymentStatus.PROVISIONING,
                connection_meta={
                    "project_id": project_id,
                    "railway_environment_id": rw_environment_id,
                    "service_id": service_id,
                },
                metadata={"project_name": project_name},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token = self._require(credentials, "api_token")
        try:
            data = self._gql(
                token,
                "query($id: String!) { service(id: $id) { id name } }",
                {"id": resource_id},
            )
        except ProviderError as exc:
            return StatusResult(status=DeploymentStatus.UNKNOWN, message=str(exc)[:200])
        if not data.get("service"):
            return StatusResult(status=DeploymentStatus.DESTROYED, message="service not found")
        return StatusResult(status=DeploymentStatus.RUNNING, raw={"service": data["service"]})

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        # resource_id is the service id; deleting the parent project is the clean teardown.
        token = self._require(credentials, "api_token")
        project_id = self._project_for_service(token, resource_id)
        if project_id:
            self._delete_project(token, project_id)

    # --- GraphQL helpers ---
    def _gql(self, token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"query": query, "variables": variables or {}}
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(_API, headers=headers, json=body)
        if resp.status_code >= 400:
            raise ProviderError(f"Railway API {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if payload.get("errors"):
            raise ProviderError(f"Railway GraphQL error: {payload['errors']}")
        return payload.get("data", {})

    def _upsert_variables(
        self,
        token: str,
        *,
        project_id: str,
        environment_id: str,
        service_id: str,
        variables: Mapping[str, str],
    ) -> None:
        for key, value in variables.items():
            try:
                self._gql(
                    token,
                    """
                    mutation($input: VariableUpsertInput!) { variableUpsert(input: $input) }
                    """,
                    {
                        "input": {
                            "projectId": project_id,
                            "environmentId": environment_id,
                            "serviceId": service_id,
                            "name": key,
                            "value": str(value),
                        }
                    },
                )
            except ProviderError as exc:
                logger.warning("railway_variable_upsert_failed", key=key, error=str(exc)[:200])

    def _project_for_service(self, token: str, service_id: str) -> str | None:
        try:
            data = self._gql(
                token,
                "query($id: String!) { service(id: $id) { projectId } }",
                {"id": service_id},
            )
            return (data.get("service") or {}).get("projectId")
        except ProviderError:
            return None

    def _delete_project(self, token: str, project_id: str) -> None:
        try:
            self._gql(
                token,
                "mutation($id: String!) { projectDelete(id: $id) }",
                {"id": project_id},
            )
        except ProviderError as exc:
            logger.warning("railway_project_delete_failed", project=project_id, error=str(exc)[:200])
