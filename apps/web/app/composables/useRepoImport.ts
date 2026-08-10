import type {
  DatastoreImportConfig,
  EnvVarOverride,
  RepoImportSaveResult,
  RepoImportSession,
  ServiceOverride,
} from '~/types/repoImport'

const IMPORT_TIMEOUT_MS = 180_000

export function useRepoImport() {
  const { apiFetch } = useApi()

  async function startImport(input: {
    git_repo_url: string
    git_branch?: string
    use_github_app_token?: boolean
    github_installation_id?: number | null
  }): Promise<RepoImportSession> {
    return apiFetch<RepoImportSession>('/imports', {
      method: 'POST',
      body: JSON.stringify({
        git_repo_url: input.git_repo_url,
        git_branch: input.git_branch || 'main',
        use_github_app_token: input.use_github_app_token ?? true,
        github_installation_id: input.github_installation_id ?? null,
      }),
      timeoutMs: IMPORT_TIMEOUT_MS,
    })
  }

  async function getImport(importId: string): Promise<RepoImportSession> {
    return apiFetch<RepoImportSession>(`/imports/${importId}`)
  }

  async function saveImport(input: {
    importId: string
    name: string
    services: ServiceOverride[]
    ensure_local_cluster?: boolean
    runtime_mode?: 'kubernetes' | 'docker_compose' | 'running_instance'
    iac_engine?: 'terraform' | 'opentofu' | 'pulumi'
    enable_iac?: boolean
    enable_cicd?: boolean
    cicd_platform?: 'github' | 'gitlab'
    project_id?: string | null
    env_vars?: EnvVarOverride[]
    datastores?: DatastoreImportConfig[]
    process_strategy?: 'docker' | 'systemd' | 'pm2'
    reverse_proxy?: 'none' | 'nginx' | 'caddy'
  }): Promise<RepoImportSaveResult> {
    return apiFetch<RepoImportSaveResult>(`/imports/${input.importId}/save`, {
      method: 'POST',
      body: JSON.stringify({
        name: input.name,
        services: input.services,
        ensure_local_cluster: input.ensure_local_cluster ?? input.runtime_mode === 'kubernetes',
        runtime_mode: input.runtime_mode ?? 'kubernetes',
        iac_engine: input.iac_engine ?? 'terraform',
        enable_iac: input.enable_iac ?? true,
        enable_cicd: input.enable_cicd ?? false,
        cicd_platform: input.cicd_platform ?? 'github',
        project_id: input.project_id || null,
        env_vars: input.env_vars ?? [],
        datastores: input.datastores ?? [],
        process_strategy: input.process_strategy ?? 'docker',
        reverse_proxy: input.reverse_proxy ?? 'none',
      }),
      timeoutMs: IMPORT_TIMEOUT_MS,
    })
  }

  async function discardImport(importId: string): Promise<void> {
    await apiFetch<void>(`/imports/${importId}`, { method: 'DELETE' })
  }

  return { startImport, getImport, saveImport, discardImport }
}
