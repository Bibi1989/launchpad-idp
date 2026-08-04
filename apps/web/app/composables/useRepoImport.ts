import type {
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
  }): Promise<RepoImportSaveResult> {
    return apiFetch<RepoImportSaveResult>(`/imports/${input.importId}/save`, {
      method: 'POST',
      body: JSON.stringify({
        name: input.name,
        services: input.services,
        ensure_local_cluster: input.ensure_local_cluster ?? true,
      }),
      timeoutMs: IMPORT_TIMEOUT_MS,
    })
  }

  async function discardImport(importId: string): Promise<void> {
    await apiFetch<void>(`/imports/${importId}`, { method: 'DELETE' })
  }

  return { startImport, getImport, saveImport, discardImport }
}
