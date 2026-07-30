import type { AuditLogEntry } from '~/types/environment'
import type {
  GitHubAppStatus,
  GitHubInstallationItem,
  GitHubRepositoryItem,
  GitHubRepositorySearchResponse,
  GitHubRepoResult,
  IaCBundleSummary,
  ImageInspectResult,
  TerminalSessionResponse,
  WorkspaceFileContent,
  WorkspaceFileNode,
  WorkspaceListItem,
  WorkspacePushRequest,
  WorkspaceTemplateInfo,
  WorkspaceWizardConfig,
} from '~/types/provisioning'
import type { GitHubRepoInput, ProvisioningWizardInput } from '~/utils/cloudValidation'
import { costOptimizationToApi } from '~/utils/costOptimization'

const PROVISION_TIMEOUT_MS = 90_000
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
    return apiFetch<IaCBundleSummary>('/provisioning/workspaces', {
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

  async function listWorkspaces(): Promise<WorkspaceListItem[]> {
    return apiFetch<WorkspaceListItem[]>('/provisioning/workspaces')
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
      timeoutMs: PROVISION_TIMEOUT_MS,
    })
  }

  async function listWorkspaceFiles(workspaceId: string): Promise<WorkspaceFileNode[]> {
    return apiFetch<WorkspaceFileNode[]>(`/provisioning/workspaces/${workspaceId}/files/tree`)
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
    },
  ): Promise<import('~/utils/workspaceFileAnalysis').WorkspaceFileAnalysisReport> {
    return apiFetch(`/provisioning/workspaces/${workspaceId}/analyze-file`, {
      method: 'POST',
      body: JSON.stringify({
        path: payload.path,
        content: payload.content,
        kind: payload.kind ?? 'auto',
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
    getWorkspace,
    listAudits,
    getWizardConfig,
    destroyWorkspace,
    openTerminal,
    listWorkspaceFiles,
    readWorkspaceFile,
    writeWorkspaceFile,
    mkdirWorkspace,
    renameWorkspacePath,
    deleteWorkspacePath,
    formatWorkspaceContent,
    listTemplates,
    applyTemplate,
    pushWorkspaceToGithub,
    analyzeWorkspaceFile,
    createGithubRepo,
    getGithubAppStatus,
    listGithubInstallations,
    listGithubRepositories,
    searchGithubRepositories,
    inspectImage,
  }
}
