import type { AuditLogEntry } from '~/types/environment'
import type {
  GitHubAppStatus,
  GitHubInstallationItem,
  GitHubRepositoryItem,
  GitHubRepositorySearchResponse,
  GitHubRepoResult,
  GitlabProjectItem,
  GitlabRepoInput,
  GitlabRepoResult,
  GitlabStatus,
  IaCBundleSummary,
  ImageInspectResult,
  TerminalSessionResponse,
  WorkspaceFileContent,
  WorkspaceFileNode,
  WorkspaceListItem,
  WorkspacePushRequest,
  WorkspacePromoteInput,
  WorkspaceTemplateInfo,
  WorkspaceWizardConfig,
  GcpApiEnablementResult,
  ProvisioningCostEstimate,
} from '~/types/provisioning'
import type { GitHubRepoInput, ProvisioningWizardInput } from '~/utils/cloudValidation'
import { costOptimizationToApi } from '~/utils/costOptimization'

const PROVISION_TIMEOUT_MS = 90_000
/** Kind/k3d cold start during open_terminal can exceed the IaC timeout. */
const LOCAL_CLUSTER_TIMEOUT_MS = 300_000
const GITHUB_TIMEOUT_MS = 60_000
/** Docker pull + inspect can take several minutes for cold images. */
const IMAGE_INSPECT_TIMEOUT_MS = 210_000

export function useProvisioning() {
  const { apiFetch } = useApi()

  async function createWorkspace(input: ProvisioningWizardInput): Promise<IaCBundleSummary> {
    const payload = {
      name: input.name,
      iac_engine: input.iac_engine,
      run_init: input.run_init,
      runtime_mode: input.runtime_mode,
      running_instance: input.running_instance,
      artifact_mode: input.artifact_mode,
      kubernetes_packaging: input.kubernetes_packaging,
      kubernetes_options: input.kubernetes_options,
      cost_optimization: costOptimizationToApi(input.cost_optimization),
      container_scaffold: input.container_scaffold,
      dependencies: input.dependencies,
      cloud: {
        provider: input.provider,
        resources: input.resources,
      },
      credentials: input.credentials,
    }
    const projectQuery = input.launchpad_project_id
      ? `?project_id=${encodeURIComponent(input.launchpad_project_id)}`
      : ''
    return apiFetch<IaCBundleSummary>(`/provisioning/workspaces${projectQuery}`, {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: PROVISION_TIMEOUT_MS,
    })
  }

  async function updateWorkspace(
    workspaceId: string,
    input: ProvisioningWizardInput,
  ): Promise<IaCBundleSummary> {
    const payload = {
      name: input.name,
      iac_engine: input.iac_engine,
      run_init: input.run_init,
      runtime_mode: input.runtime_mode,
      running_instance: input.running_instance,
      artifact_mode: input.artifact_mode,
      kubernetes_packaging: input.kubernetes_packaging,
      kubernetes_options: input.kubernetes_options,
      cost_optimization: costOptimizationToApi(input.cost_optimization),
      container_scaffold: input.container_scaffold,
      dependencies: input.dependencies,
      cloud: {
        provider: input.provider,
        resources: input.resources,
      },
      credentials: input.credentials,
    }
    return apiFetch<IaCBundleSummary>(`/provisioning/workspaces/${workspaceId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
      timeoutMs: PROVISION_TIMEOUT_MS,
    })
  }

  async function listWorkspaces(opts?: {
    starred?: boolean
    projectId?: string | null
  }): Promise<WorkspaceListItem[]> {
    const params = new URLSearchParams()
    if (opts?.starred) params.set('starred', 'true')
    if (opts?.projectId) params.set('project_id', opts.projectId)
    const query = params.toString() ? `?${params.toString()}` : ''
    return apiFetch<WorkspaceListItem[]>(`/provisioning/workspaces${query}`)
  }

  async function setWorkspaceStarred(
    workspaceId: string,
    starred: boolean,
  ): Promise<WorkspaceListItem> {
    return apiFetch<WorkspaceListItem>(`/provisioning/workspaces/${workspaceId}/star`, {
      method: 'PUT',
      body: JSON.stringify({ starred }),
    })
  }

  async function getWorkspace(workspaceId: string): Promise<IaCBundleSummary> {
    return apiFetch<IaCBundleSummary>(`/provisioning/workspaces/${workspaceId}`)
  }

  async function listAudits(workspaceId: string, limit = 50): Promise<AuditLogEntry[]> {
    return apiFetch<AuditLogEntry[]>(
      `/provisioning/workspaces/${workspaceId}/audits?limit=${limit}`,
    )
  }

  async function getWizardConfig(workspaceId: string): Promise<WorkspaceWizardConfig> {
    return apiFetch<WorkspaceWizardConfig>(`/provisioning/workspaces/${workspaceId}/config`)
  }

  async function promoteWorkspace(
    workspaceId: string,
    input: WorkspacePromoteInput,
  ): Promise<IaCBundleSummary> {
    return apiFetch<IaCBundleSummary>(`/provisioning/workspaces/${workspaceId}/promote`, {
      method: 'POST',
      body: JSON.stringify(input),
      timeoutMs: PROVISION_TIMEOUT_MS,
    })
  }

  async function estimateProvisioningCost(
    input: ProvisioningWizardInput,
  ): Promise<ProvisioningCostEstimate> {
    const payload = {
      name: input.name,
      iac_engine: input.iac_engine,
      run_init: input.run_init,
      runtime_mode: input.runtime_mode,
      running_instance: input.running_instance,
      artifact_mode: input.artifact_mode,
      kubernetes_packaging: input.kubernetes_packaging,
      kubernetes_options: input.kubernetes_options,
      cost_optimization: costOptimizationToApi(input.cost_optimization),
      container_scaffold: input.container_scaffold,
      dependencies: input.dependencies,
      cloud: {
        provider: input.provider,
        resources: input.resources,
      },
      credentials: input.credentials,
    }
    return apiFetch<ProvisioningCostEstimate>('/provisioning/estimate-cost', {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    })
  }

  async function enableCloudApis(workspaceId: string): Promise<GcpApiEnablementResult> {
    return apiFetch<GcpApiEnablementResult>(
      `/provisioning/workspaces/${workspaceId}/enable-cloud-apis`,
      {
        method: 'POST',
        timeoutMs: PROVISION_TIMEOUT_MS * 3,
      },
    )
  }

  async function destroyWorkspace(workspaceId: string): Promise<void> {
    await apiFetch<void>(`/provisioning/workspaces/${workspaceId}`, {
      method: 'DELETE',
    })
  }

  async function openTerminal(
    workspaceId: string,
    opts: { cols?: number; rows?: number; run_init?: boolean } = {},
  ): Promise<TerminalSessionResponse> {
    return apiFetch<TerminalSessionResponse>(`/provisioning/workspaces/${workspaceId}/terminal`, {
      method: 'POST',
      body: JSON.stringify({
        cols: opts.cols ?? 120,
        rows: opts.rows ?? 40,
        run_init: opts.run_init ?? true,
      }),
      // Local Kubernetes may run ensure_kind_cluster here (cold cluster).
      timeoutMs: LOCAL_CLUSTER_TIMEOUT_MS,
    })
  }

  async function listWorkspaceFiles(workspaceId: string): Promise<WorkspaceFileNode[]> {
    return apiFetch<WorkspaceFileNode[]>(`/provisioning/workspaces/${workspaceId}/files/tree`)
  }

  async function restoreWorkspaceFiles(workspaceId: string): Promise<IaCBundleSummary> {
    return apiFetch<IaCBundleSummary>(`/provisioning/workspaces/${workspaceId}/restore-files`, {
      method: 'POST',
    })
  }

  async function readWorkspaceFile(
    workspaceId: string,
    path: string,
    signal?: AbortSignal,
  ): Promise<WorkspaceFileContent> {
    return apiFetch<WorkspaceFileContent>(
      `/provisioning/workspaces/${workspaceId}/files?path=${encodeURIComponent(path)}`,
      { signal },
    )
  }

  async function writeWorkspaceFile(
    workspaceId: string,
    path: string,
    content: string,
  ): Promise<WorkspaceFileContent> {
    return apiFetch<WorkspaceFileContent>(`/provisioning/workspaces/${workspaceId}/files`, {
      method: 'PUT',
      body: JSON.stringify({ path, content }),
    })
  }

  async function mkdirWorkspace(workspaceId: string, path: string): Promise<WorkspaceFileNode> {
    return apiFetch<WorkspaceFileNode>(`/provisioning/workspaces/${workspaceId}/files/mkdir`, {
      method: 'POST',
      body: JSON.stringify({ path }),
    })
  }

  async function renameWorkspacePath(
    workspaceId: string,
    fromPath: string,
    toPath: string,
  ): Promise<WorkspaceFileNode> {
    return apiFetch<WorkspaceFileNode>(`/provisioning/workspaces/${workspaceId}/files/rename`, {
      method: 'POST',
      body: JSON.stringify({ from_path: fromPath, to_path: toPath }),
    })
  }

  async function deleteWorkspacePath(workspaceId: string, path: string): Promise<void> {
    await apiFetch<void>(
      `/provisioning/workspaces/${workspaceId}/files?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' },
    )
  }

  async function formatWorkspaceContent(
    workspaceId: string,
    path: string,
    content: string,
  ): Promise<WorkspaceFileContent> {
    return apiFetch<WorkspaceFileContent>(`/provisioning/workspaces/${workspaceId}/files/format`, {
      method: 'POST',
      body: JSON.stringify({ path, content }),
    })
  }

  async function listTemplates(category?: string): Promise<WorkspaceTemplateInfo[]> {
    const query = category ? `?category=${encodeURIComponent(category)}` : ''
    return apiFetch<WorkspaceTemplateInfo[]>(`/provisioning/templates${query}`)
  }

  async function applyTemplate(
    workspaceId: string,
    templateId: string,
    path?: string,
    overwrite = false,
  ): Promise<WorkspaceFileContent> {
    return apiFetch<WorkspaceFileContent>(
      `/provisioning/workspaces/${workspaceId}/files/from-template`,
      {
        method: 'POST',
        body: JSON.stringify({
          template_id: templateId,
          path: path ?? null,
          overwrite,
        }),
      },
    )
  }

  async function pushWorkspaceToGithub(
    workspaceId: string,
    payload: WorkspacePushRequest,
  ): Promise<GitHubRepoResult> {
    return apiFetch<GitHubRepoResult>(`/provisioning/workspaces/${workspaceId}/github/push`, {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: GITHUB_TIMEOUT_MS,
    })
  }

  async function analyzeWorkspaceFile(
    workspaceId: string,
    payload: {
      path: string
      content: string
      kind?: 'auto' | 'cicd' | 'docker' | 'iac' | 'kubernetes'
      error_context?: string | null
    },
  ): Promise<import('~/utils/workspaceFileAnalysis').WorkspaceFileAnalysisReport> {
    return apiFetch(`/provisioning/workspaces/${workspaceId}/analyze-file`, {
      method: 'POST',
      body: JSON.stringify({
        path: payload.path,
        content: payload.content,
        kind: payload.kind ?? 'auto',
        error_context: payload.error_context ?? null,
      }),
      timeoutMs: 90_000,
    })
  }

  async function createGithubRepo(
    input: GitHubRepoInput,
    credentials: Record<string, string | null | undefined> = {},
  ): Promise<GitHubRepoResult> {
    return apiFetch<GitHubRepoResult>('/provisioning/github/repositories', {
      method: 'POST',
      body: JSON.stringify({
        ...input,
        credentials,
      }),
      timeoutMs: GITHUB_TIMEOUT_MS,
    })
  }

  async function getGithubAppStatus(): Promise<GitHubAppStatus> {
    return apiFetch<GitHubAppStatus>('/provisioning/github/status')
  }

  async function getGitlabStatus(): Promise<GitlabStatus> {
    return apiFetch<GitlabStatus>('/provisioning/gitlab/status')
  }

  async function connectGitlabPat(token: string, baseUrl?: string): Promise<GitlabStatus> {
    return apiFetch<GitlabStatus>('/provisioning/gitlab/connect/pat', {
      method: 'POST',
      body: JSON.stringify({
        token,
        base_url: baseUrl || null,
      }),
    })
  }

  async function completeGitlabOAuth(code: string, state: string): Promise<GitlabStatus> {
    return apiFetch<GitlabStatus>('/provisioning/gitlab/oauth/callback', {
      method: 'POST',
      body: JSON.stringify({ code, state }),
    })
  }

  async function disconnectGitlab(): Promise<void> {
    await apiFetch('/provisioning/gitlab/connection', { method: 'DELETE' })
  }

  async function listGitlabProjects(opts?: { q?: string }): Promise<GitlabProjectItem[]> {
    const params = new URLSearchParams()
    if (opts?.q?.trim()) params.set('q', opts.q.trim())
    const qs = params.toString()
    return apiFetch<GitlabProjectItem[]>(
      `/provisioning/gitlab/projects${qs ? `?${qs}` : ''}`,
    )
  }

  async function createGitlabRepo(input: GitlabRepoInput): Promise<GitlabRepoResult> {
    return apiFetch<GitlabRepoResult>('/provisioning/gitlab/repositories', {
      method: 'POST',
      body: JSON.stringify(input),
      timeoutMs: GITHUB_TIMEOUT_MS,
    })
  }

  async function pushWorkspaceToGitlab(
    workspaceId: string,
    payload: { project_path: string; commit_message?: string },
  ): Promise<GitlabRepoResult> {
    return apiFetch<GitlabRepoResult>(`/provisioning/workspaces/${workspaceId}/gitlab/push`, {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: GITHUB_TIMEOUT_MS,
    })
  }

  async function listGithubInstallations(): Promise<GitHubInstallationItem[]> {
    return apiFetch<GitHubInstallationItem[]>('/provisioning/github/installations')
  }

  async function listGithubRepositories(installationId: number): Promise<GitHubRepositoryItem[]> {
    return apiFetch<GitHubRepositoryItem[]>(
      `/provisioning/github/installations/${installationId}/repositories`,
    )
  }

  async function searchGithubRepositories(opts?: {
    q?: string
    page?: number
    perPage?: number
    installationId?: number | null
  }): Promise<GitHubRepositorySearchResponse> {
    const params = new URLSearchParams()
    if (opts?.q) params.set('q', opts.q)
    if (opts?.page) params.set('page', String(opts.page))
    if (opts?.perPage) params.set('per_page', String(opts.perPage))
    if (opts?.installationId) params.set('installation_id', String(opts.installationId))
    const queryStr = params.toString() ? `?${params.toString()}` : ''
    return apiFetch<GitHubRepositorySearchResponse>(`/provisioning/github/repositories${queryStr}`)
  }

  async function inspectImage(image: string): Promise<ImageInspectResult> {
    return apiFetch<ImageInspectResult>('/provisioning/images/inspect', {
      method: 'POST',
      body: JSON.stringify({ image }),
      timeoutMs: IMAGE_INSPECT_TIMEOUT_MS,
    })
  }

  return {
    createWorkspace,
    updateWorkspace,
    listWorkspaces,
    setWorkspaceStarred,
    getWorkspace,
    listAudits,
    getWizardConfig,
    promoteWorkspace,
    estimateProvisioningCost,
    enableCloudApis,
    destroyWorkspace,
    openTerminal,
    listWorkspaceFiles,
    restoreWorkspaceFiles,
    readWorkspaceFile,
    writeWorkspaceFile,
    mkdirWorkspace,
    renameWorkspacePath,
    deleteWorkspacePath,
    formatWorkspaceContent,
    listTemplates,
    applyTemplate,
    pushWorkspaceToGithub,
    pushWorkspaceToGitlab,
    analyzeWorkspaceFile,
    createGithubRepo,
    createGitlabRepo,
    getGithubAppStatus,
    getGitlabStatus,
    connectGitlabPat,
    completeGitlabOAuth,
    disconnectGitlab,
    listGitlabProjects,
    listGithubInstallations,
    listGithubRepositories,
    searchGithubRepositories,
    inspectImage,
  }
}
