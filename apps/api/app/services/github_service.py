from __future__ import annotations

from github import Auth, Github, GithubException
from github.Repository import Repository

from app.core.logging import get_logger
from app.schemas.cloud import (
    CloudCredentials,
    CloudProvider,
    GitHubRepoRequest,
    GitHubRepoResult,
    IaCEngine,
)
from app.services.github_app import (
    GitHubAppAuthError,
    get_installation_client,
    is_github_app_configured,
)
from app.services.iac_generator import IaCGenerator

logger = get_logger(__name__)

_DEFAULT_WORKFLOW_PATH = ".github/workflows/deploy.yml"
_FALLBACK_WORKFLOW_PATHS = (
    ".github/workflows/launchpad-deploy.yml",
    ".github/workflows/launchpad-infra.yml",
)

def _workspace_has_dockerfile(files: dict[str, str]) -> bool:
    for path in files:
        name = path.rsplit("/", 1)[-1].lower()
        if name == "dockerfile" or name.startswith("dockerfile."):
            return True
        if "/dockers/" in f"/{path}" and "dockerfile" in name:
            return True
    return False


def _workspace_has_ci_workflow(files: dict[str, str]) -> bool:
    return any(
        path.startswith(".github/workflows/")
        or path.startswith("ci/github/workflows/")
        or path.endswith("/.gitlab-ci.yml")
        or path == ".gitlab-ci.yml"
        or path.startswith("ci/gitlab/")
        for path in files
    )




class GitHubProvisioningService:
    """Creates or opens repos, sets encrypted CI secrets, and commits infra/workflows."""

    def __init__(self, iac_generator: IaCGenerator | None = None) -> None:
        self._iac = iac_generator or IaCGenerator()

    def create_repository_with_workflow(
        self,
        request: GitHubRepoRequest,
        *,
        root_dir: str | None = None,
    ) -> GitHubRepoResult:
        client, installation_id, account_login, account_type, auth_method = (
            self._authenticate(request)
        )

        logger.info(
            "github_repo_bootstrap_start",
            repo=request.name,
            existing_full_name=request.existing_full_name,
            org=request.organization or account_login or None,
            installation_id=installation_id,
            account_type=account_type or None,
            auth_method=auth_method,
            include_workflow=request.include_workflow,
        )

        try:
            repo, created = self._resolve_repository(
                client,
                request=request,
                account_login=account_login,
                account_type=account_type,
                auth_method=auth_method,
            )
        except GithubException as exc:
            logger.error(
                "github_repo_resolve_failed",
                status=exc.status,
                message=str(exc.data) if exc.data else str(exc),
                account_type=account_type or None,
            )
            raise ValueError(self._friendly_github_error(exc, account_type=account_type)) from exc

        secrets_set: list[str] = []
        if request.set_cloud_secrets:
            secrets_set = self._set_repository_secrets(repo, request.credentials)

        provider, engine, files = self._resolve_bundle(
            request.workspace_id,
            root_dir=root_dir,
        )

        # Mirror workspace paths exactly - never rewrite under infra/.
        commit_payload: dict[str, str] = dict(files)

        workflow_path: str | None = None
        if request.include_workflow and not _workspace_has_ci_workflow(files):
            workflow_path = self._allocate_workflow_path(repo)
            workflow = _render_workflow(
                provider=provider,
                engine=engine,
                workflow_path=workflow_path,
            )
            commit_payload[workflow_path] = workflow

        if getattr(request, "include_dockerfiles", False) and not _workspace_has_dockerfile(files):
            from app.services.dockerfile_scaffold import (
                default_dockerfile_path,
                detect_stack,
                scaffold_dockerfile,
            )

            paths = list(files.keys())
            stack, _ = detect_stack(paths)
            dockerfile_content = scaffold_dockerfile(stack, app_name=repo.name)
            commit_payload[default_dockerfile_path()] = dockerfile_content

        if "README.md" not in commit_payload and (
            created or not self._path_exists(repo, "README.md")
        ):
            commit_payload["README.md"] = _readme(
                repo.full_name,
                provider,
                engine,
                workflow_path=workflow_path,
            )

        if not commit_payload:
            logger.info(
                "github_repo_bootstrap_noop",
                full_name=repo.full_name,
                reason="no_infra_or_workflow_to_commit",
            )
            return GitHubRepoResult(
                full_name=repo.full_name,
                html_url=repo.html_url,
                private=repo.private,
                default_branch=repo.default_branch,
                secrets_set=secrets_set,
                workflow_path=workflow_path,
                installation_id=installation_id,
                auth_method=auth_method,
                created=created,
            )

        try:
            self._commit_files(
                repo,
                commit_payload,
                message="chore: bootstrap Launchpad infra and deploy workflow",
            )
        except (GithubException, AssertionError, TypeError, ValueError) as exc:
            status = getattr(exc, "status", None)
            logger.error(
                "github_commit_failed",
                status=status,
                message=str(getattr(exc, "data", None) or exc),
                repo=repo.full_name,
            )
            raise ValueError(
                f"Repository {repo.html_url} was resolved, but committing workflow/infra failed: "
                f"{self._friendly_github_error(exc) if isinstance(exc, GithubException) else str(exc)}"
            ) from exc

        logger.info(
            "github_repo_bootstrap_success",
            full_name=repo.full_name,
            secrets_set=secrets_set,
            workflow_path=workflow_path,
            installation_id=installation_id,
            auth_method=auth_method,
            created=created,
            files_committed=len(commit_payload),
        )
        return GitHubRepoResult(
            full_name=repo.full_name,
            html_url=repo.html_url,
            private=repo.private,
            default_branch=repo.default_branch,
            secrets_set=secrets_set,
            workflow_path=workflow_path,
            installation_id=installation_id,
            auth_method=auth_method,
            created=created,
        )

    def _authenticate(
        self, request: GitHubRepoRequest
    ) -> tuple[Github, int | None, str, str, str]:
        from app.core.config import get_settings
        from app.core.secrets import mask_secret_value

        if is_github_app_configured():
            try:
                client, installation_id, account_login, account_type = get_installation_client(
                    installation_id=request.installation_id,
                    organization=request.organization,
                )
            except GitHubAppAuthError as exc:
                raise ValueError(str(exc)) from exc
            return client, installation_id, account_login, account_type, "github_app"

        # Deprecated emergency fallback for local smoke tests only.
        token = (get_settings().github_pat or "").strip()
        if token:
            logger.warning(
                "github_pat_fallback_used",
                token_fingerprint=mask_secret_value(token),
            )
            return Github(auth=Auth.Token(token)), None, "", "User", "github_pat"

        raise ValueError(
            "GitHub App is not configured - set GITHUB_APP_ID and "
            "GITHUB_APP_PRIVATE_KEY (or GITHUB_APP_PRIVATE_KEY_PATH) on the API"
        )

    def _resolve_repository(
        self,
        client: Github,
        *,
        request: GitHubRepoRequest,
        account_login: str,
        account_type: str,
        auth_method: str,
    ) -> tuple[Repository, bool]:
        if request.existing_full_name:
            try:
                repo = client.get_repo(request.existing_full_name)
            except GithubException as exc:
                if exc.status == 404:
                    raise ValueError(
                        f"Repository '{request.existing_full_name}' was not found or is not "
                        "accessible to this GitHub App installation"
                    ) from exc
                raise
            logger.info(
                "github_repo_imported",
                full_name=repo.full_name,
            )
            return repo, False

        return self._create_or_open_repository(
            client,
            request=request,
            account_login=account_login,
            account_type=account_type,
            auth_method=auth_method,
        )

    def _create_or_open_repository(
        self,
        client: Github,
        *,
        request: GitHubRepoRequest,
        account_login: str,
        account_type: str,
        auth_method: str,
    ) -> tuple[Repository, bool]:
        owner_login = (request.organization or account_login or "").strip()
        is_organization = account_type.lower() == "organization"

        # Organization installations can create repos with Administration:write.
        if is_organization or auth_method == "github_pat":
            if owner_login and is_organization:
                owner = client.get_organization(owner_login)
                repo = owner.create_repo(
                    name=request.name,
                    description=request.description,
                    private=request.private,
                    auto_init=True,
                )
                return repo, True
            user = client.get_user()
            repo = user.create_repo(
                name=request.name,
                description=request.description,
                private=request.private,
                auto_init=True,
            )
            return repo, True

        # Personal-account GitHub App installations cannot call POST /user/repos
        # with an installation token (GitHub returns 403 Resource not accessible
        # by integration). Bootstrap an existing empty repo instead.
        if not owner_login:
            raise ValueError(
                "GitHub App is installed on a personal account but no account login "
                "was resolved - reconnect GitHub and retry"
            )

        full_name = f"{owner_login}/{request.name}"
        try:
            repo = client.get_repo(full_name)
        except GithubException as exc:
            if exc.status != 404:
                raise
            raise ValueError(
                "GitHub Apps cannot create new repositories on personal accounts "
                f"with an installation token. Create an empty repo named '{request.name}' "
                f"at https://github.com/new?name={request.name}, grant the App access to it, "
                "then retry Create (or use Import Git Repository). To create repos via API, "
                "install the App on a GitHub Organization instead."
            ) from exc

        logger.info(
            "github_repo_reused_personal_account",
            full_name=repo.full_name,
            account=owner_login,
        )
        return repo, False

    def _resolve_bundle(
        self,
        workspace_id: str | None,
        *,
        root_dir: str | None = None,
    ) -> tuple[CloudProvider, IaCEngine, dict[str, str]]:
        if not workspace_id and not root_dir:
            return CloudProvider.GCP, IaCEngine.TERRAFORM, {}
        files = self._iac.read_bundle_files(root_dir or workspace_id or "")
        engine = IaCEngine.PULUMI if "Pulumi.yaml" in files else IaCEngine.TERRAFORM
        provider = self._detect_provider(files)
        return provider, engine, files

    def _detect_provider(self, files: dict[str, str]) -> CloudProvider:
        joined = "\n".join(files.values()).lower()
        if "azurerm" in joined or "@pulumi/azure" in joined:
            return CloudProvider.AZURE
        if "cloudflare" in joined:
            return CloudProvider.CLOUDFLARE
        if 'provider "aws"' in joined or "@pulumi/aws" in joined:
            return CloudProvider.AWS
        return CloudProvider.GCP

    def _allocate_workflow_path(self, repo: Repository) -> str:
        """Pick a new workflow path; never overwrite an existing workflow file."""
        if not self._path_exists(repo, _DEFAULT_WORKFLOW_PATH):
            return _DEFAULT_WORKFLOW_PATH

        for candidate in _FALLBACK_WORKFLOW_PATHS:
            if not self._path_exists(repo, candidate):
                logger.info(
                    "github_workflow_path_allocated",
                    preferred=_DEFAULT_WORKFLOW_PATH,
                    allocated=candidate,
                    reason="preferred_exists",
                )
                return candidate

        index = 2
        while True:
            candidate = f".github/workflows/launchpad-deploy-{index}.yml"
            if not self._path_exists(repo, candidate):
                logger.info(
                    "github_workflow_path_allocated",
                    preferred=_DEFAULT_WORKFLOW_PATH,
                    allocated=candidate,
                    reason="fallbacks_exist",
                )
                return candidate
            index += 1

    def _path_exists(self, repo: Repository, path: str) -> bool:
        try:
            repo.get_contents(path)
            return True
        except GithubException as exc:
            if exc.status == 404:
                return False
            raise

    def _set_repository_secrets(
        self, repo: Repository, credentials: CloudCredentials
    ) -> list[str]:
        mapping = {
            "GCP_SA_KEY": credentials.gcp_sa_key_json,
            "GCP_WIF_PROJECT_NUMBER": credentials.gcp_wif_project_number,
            "GCP_WIF_POOL_ID": credentials.gcp_wif_pool_id,
            "GCP_WIF_PROVIDER_ID": credentials.gcp_wif_provider_id,
            "GCP_WIF_TARGET_SA_EMAIL": credentials.gcp_wif_target_sa_email,
            "AWS_ACCESS_KEY_ID": credentials.aws_access_key_id,
            "AWS_SECRET_ACCESS_KEY": credentials.aws_secret_access_key,
            "AWS_SESSION_TOKEN": credentials.aws_session_token,
            "AWS_ROLE_ARN": credentials.aws_role_arn,
            "AWS_ROLE_SESSION_NAME": credentials.aws_role_session_name,
            "AZURE_CLIENT_ID": credentials.azure_client_id,
            "AZURE_CLIENT_SECRET": credentials.azure_client_secret,
            "AZURE_TENANT_ID": credentials.azure_tenant_id,
            "AZURE_SUBSCRIPTION_ID": credentials.azure_subscription_id,
            "CLOUDFLARE_API_TOKEN": credentials.cloudflare_api_token,
        }
        set_names: list[str] = []
        for name, value in mapping.items():
            if not value:
                continue
            try:
                repo.create_secret(name, value)
                set_names.append(name)
            except GithubException as exc:
                logger.error(
                    "github_secret_set_failed",
                    secret_name=name,
                    status=exc.status,
                )
                raise ValueError(f"Failed to set repository secret '{name}'") from exc

        if (
            credentials.azure_client_id
            and credentials.azure_client_secret
            and credentials.azure_tenant_id
            and credentials.azure_subscription_id
        ):
            import json

            azure_creds = json.dumps(
                {
                    "clientId": credentials.azure_client_id,
                    "clientSecret": credentials.azure_client_secret,
                    "tenantId": credentials.azure_tenant_id,
                    "subscriptionId": credentials.azure_subscription_id,
                }
            )
            try:
                repo.create_secret("AZURE_CREDENTIALS", azure_creds)
                set_names.append("AZURE_CREDENTIALS")
            except GithubException as exc:
                raise ValueError("Failed to set repository secret 'AZURE_CREDENTIALS'") from exc

        return set_names

    def _commit_files(
        self,
        repo: Repository,
        files: dict[str, str],
        *,
        message: str = "chore: bootstrap Launchpad infra and deploy workflow",
    ) -> None:
        from github import InputGitTreeElement

        default_branch = repo.default_branch
        ref = repo.get_git_ref(f"heads/{default_branch}")
        base_sha = ref.object.sha
        base_tree = repo.get_git_tree(base_sha)
        element_list = [
            InputGitTreeElement(
                path=path,
                mode="100644",
                type="blob",
                sha=repo.create_git_blob(content, "utf-8").sha,
            )
            for path, content in files.items()
        ]
        tree = repo.create_git_tree(element_list, base_tree)
        parent = repo.get_git_commit(base_sha)
        commit = repo.create_git_commit(
            message,
            tree,
            [parent],
        )
        ref.edit(commit.sha)

    def push_workspace_files(
        self,
        *,
        installation_id: int,
        existing_full_name: str,
        root_dir: str,
        commit_message: str,
        include_workflow: bool = False,
        include_dockerfiles: bool = False,
        provider: CloudProvider | None = None,
        engine: IaCEngine | None = None,
    ) -> GitHubRepoResult:
        """Commit current workspace files into an existing GitHub repository."""
        client, resolved_installation_id, _account_login, _account_type = get_installation_client(
            installation_id=installation_id,
        )
        try:
            repo = client.get_repo(existing_full_name)
        except GithubException as exc:
            raise ValueError(
                f"Unable to open repository {existing_full_name}: "
                f"{self._friendly_github_error(exc)}"
            ) from exc

        files = self._iac.read_bundle_files(root_dir)
        if not files:
            raise ValueError("Workspace has no files to push")

        detected_provider, detected_engine, _ = self._resolve_bundle(
            None, root_dir=root_dir
        )
        use_provider = provider or detected_provider
        use_engine = engine or detected_engine

        # Mirror workspace paths exactly - never rewrite under infra/.
        commit_payload: dict[str, str] = dict(files)

        workflow_path: str | None = None
        if include_workflow and not _workspace_has_ci_workflow(files):
            workflow_path = self._allocate_workflow_path(repo)
            commit_payload[workflow_path] = _render_workflow(
                provider=use_provider,
                engine=use_engine,
                workflow_path=workflow_path,
            )

        if include_dockerfiles and not _workspace_has_dockerfile(files):
            from app.services.dockerfile_scaffold import (
                default_dockerfile_path,
                detect_stack,
                scaffold_dockerfile,
            )

            paths = list(files.keys())
            stack, _ = detect_stack(paths)
            dockerfile_content = scaffold_dockerfile(stack, app_name=repo.name)
            commit_payload[default_dockerfile_path()] = dockerfile_content

        try:
            self._commit_files(repo, commit_payload, message=commit_message)
        except (GithubException, AssertionError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Failed to push workspace files: "
                f"{self._friendly_github_error(exc) if isinstance(exc, GithubException) else str(exc)}"
            ) from exc

        logger.info(
            "github_workspace_push_success",
            full_name=repo.full_name,
            files_committed=len(commit_payload),
            installation_id=resolved_installation_id,
        )
        return GitHubRepoResult(
            full_name=repo.full_name,
            html_url=repo.html_url,
            private=repo.private,
            default_branch=repo.default_branch,
            secrets_set=[],
            workflow_path=workflow_path,
            installation_id=resolved_installation_id,
            auth_method="github_app",
            created=False,
        )

    @staticmethod
    def _friendly_github_error(
        exc: GithubException,
        *,
        account_type: str | None = None,
    ) -> str:
        detail = ""
        if isinstance(exc.data, dict):
            message = exc.data.get("message")
            if isinstance(message, str) and message.strip():
                detail = f" - {message.strip()}"

        if exc.status == 401:
            return (
                "GitHub App authentication failed - check GITHUB_APP_ID and private key"
                + detail
            )
        if exc.status == 403:
            if (account_type or "").lower() == "user" or (
                isinstance(exc.data, dict)
                and "create-a-repository-for-the-authenticated-user"
                in str(exc.data.get("documentation_url") or "")
            ):
                return (
                    "GitHub Apps cannot create repositories on personal accounts with an "
                    "installation token. Create an empty repo on GitHub first (or Import an "
                    "existing one), or install the App on a GitHub Organization"
                    + detail
                )
            return (
                "GitHub App lacks required repository permissions. In the App settings, set "
                "Administration (read/write), Contents (read/write), Secrets (read/write), "
                "and Metadata (read), then accept the permission update on the installation"
                + detail
            )
        if exc.status == 404:
            return (
                "GitHub resource not found - confirm the App is installed on the target "
                "account/org and can access the repository"
                + detail
            )
        if exc.status == 422:
            return (
                "GitHub rejected the repository create request "
                "(name may already exist)"
                + detail
            )
        return f"GitHub API request failed{detail}"


def _render_workflow(
    *,
    provider: CloudProvider,
    engine: IaCEngine,
    workflow_path: str,
) -> str:
    if engine == IaCEngine.PULUMI:
        return _pulumi_workflow(provider, workflow_path=workflow_path)
    if engine == IaCEngine.OPENTOFU:
        return _opentofu_workflow(provider, workflow_path=workflow_path)
    return _terraform_workflow(provider, workflow_path=workflow_path)


def _opentofu_workflow(provider: CloudProvider, *, workflow_path: str) -> str:
    auth_steps = _auth_steps(provider)
    return f"""\
name: Deploy Infrastructure (OpenTofu)

on:
  push:
    branches: [main]
    paths:
      - "infra/**"
      - "{workflow_path}"
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  opentofu:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: infra/terraform
    steps:
      - uses: actions/checkout@v4
      - uses: opentofu/setup-opentofu@v1
        with:
          tofu_version: "1.8.0"
{auth_steps}
      - name: OpenTofu Init
        run: tofu init -input=false
      - name: OpenTofu Plan
        run: tofu plan -input=false -no-color
      - name: OpenTofu Apply
        if: github.ref == 'refs/heads/main'
        run: tofu apply -auto-approve -input=false
"""


def _terraform_workflow(provider: CloudProvider, *, workflow_path: str) -> str:
    auth_steps = _auth_steps(provider)
    return f"""\
name: Deploy Infrastructure

on:
  push:
    branches: [main]
    paths:
      - "infra/**"
      - "{workflow_path}"
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  terraform:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: infra/terraform
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.9.0"
{auth_steps}
      - name: Terraform Init
        run: terraform init -input=false
      - name: Terraform Plan
        run: terraform plan -input=false -no-color
      - name: Terraform Apply
        if: github.ref == 'refs/heads/main'
        run: terraform apply -auto-approve -input=false
"""


def _pulumi_workflow(provider: CloudProvider, *, workflow_path: str) -> str:
    auth_steps = _auth_steps(provider)
    return f"""\
name: Deploy Infrastructure

on:
  push:
    branches: [main]
    paths:
      - "infra/**"
      - "{workflow_path}"
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  pulumi:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: infra
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
{auth_steps}
      - name: Install dependencies
        run: npm install
      - uses: pulumi/actions@v5
        with:
          command: up
          stack-name: launchpad/prod
          work-dir: infra
        env:
          PULUMI_ACCESS_TOKEN: ${{{{ secrets.PULUMI_ACCESS_TOKEN }}}}
"""


def _auth_steps(provider: CloudProvider) -> str:
    if provider == CloudProvider.LOCAL:
        return """\
      - name: Use local kubeconfig (kind)
        run: |
          echo "Dev (kind) workspaces expect kubectl context kind-launchpad"
          kubectl config current-context || true
"""
    if provider == CloudProvider.GCP:
        return """\
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      - uses: google-github-actions/setup-gcloud@v2
"""
    if provider == CloudProvider.AWS:
        return """\
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-session-token: ${{ secrets.AWS_SESSION_TOKEN }}
          aws-region: us-east-1
"""
    if provider == CloudProvider.AZURE:
        return """\
      - name: Azure Login
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
"""
    return """\
      - name: Configure Cloudflare
        run: echo "CLOUDFLARE_API_TOKEN configured"
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
"""


def _readme(
    full_name: str,
    provider: CloudProvider,
    engine: IaCEngine,
    *,
    workflow_path: str | None,
) -> str:
    workflow_line = (
        f"- Workflow: `{workflow_path}`\n" if workflow_path else "- Workflow: (not added)\n"
    )
    return (
        f"# {full_name}\n\n"
        f"Bootstrapped by Launchpad IDP.\n\n"
        f"- Provider: `{provider.value}`\n"
        f"- Engine: `{engine.value}`\n"
        f"{workflow_line}"
    )
