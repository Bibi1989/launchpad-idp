import type { Environment, LifecycleStage } from '~/types/environment'

const STAGE_RANK: Record<string, number> = {
  preview: 0,
  staging: 1,
  production: 2,
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export interface EnvironmentLineageGroup {
  lineageId: string
  title: string
  subtitle: string | null
  environments: Environment[]
}

function stageRank(env: Environment): number {
  const stage = String(env.lifecycle_stage || 'preview').toLowerCase()
  return STAGE_RANK[stage] ?? 0
}

/** Stable key for grouping promoted environments of the same app. */
export function environmentLineageKey(env: Environment): string {
  return (env.promotion_lineage_id || env.id || '').trim() || env.id
}

/** Synthetic URL used when a workspace has no linked git remote. */
export function isWorkspaceFallbackRepoUrl(gitRepoUrl: string | null | undefined): boolean {
  const raw = (gitRepoUrl || '').trim()
  if (!raw) return false
  return /\/workspaces\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(
    raw,
  )
}

function shortRepoName(gitRepoUrl: string | null | undefined): string | null {
  const raw = (gitRepoUrl || '').trim()
  if (!raw) return null
  try {
    const withoutGit = raw.replace(/\.git$/i, '')
    const path = withoutGit.includes('://')
      ? new URL(withoutGit).pathname
      : withoutGit.includes(':')
        ? withoutGit.split(':').slice(1).join(':')
        : withoutGit
    const parts = path.split('/').filter(Boolean)
    const name = parts[parts.length - 1]
    return name || null
  } catch {
    const parts = raw.replace(/\.git$/i, '').split('/').filter(Boolean)
    return parts[parts.length - 1] || null
  }
}

function stripStageSuffix(name: string): string {
  return name.replace(/-(staging|production|prod|preview)$/i, '') || name
}

function workspaceName(envs: Environment[]): string | null {
  for (const env of envs) {
    const name = (env.workspace_name || '').trim()
    if (name) return name
  }
  return null
}

function groupTitle(envs: Environment[]): string {
  const fromWorkspace = workspaceName(envs)
  if (fromWorkspace) return fromWorkspace

  const url = envs[0]?.git_repo_url
  if (!isWorkspaceFallbackRepoUrl(url)) {
    const fromRepo = shortRepoName(url)
    if (fromRepo && !UUID_RE.test(fromRepo)) return fromRepo
  }

  const preferred =
    envs.find((e) => (e.lifecycle_stage || 'preview') === 'preview')
    || envs.find((e) => e.lifecycle_stage === 'staging')
    || envs[0]
  return stripStageSuffix(preferred?.name || 'app')
}

function groupSubtitle(envs: Environment[]): string | null {
  const url = (envs[0]?.git_repo_url || '').trim()
  if (!url || isWorkspaceFallbackRepoUrl(url)) {
    return null
  }
  try {
    if (url.startsWith('git@')) {
      return url.replace(/^git@/, '').replace(':', '/')
    }
    const parsed = new URL(url)
    return `${parsed.host}${parsed.pathname.replace(/\.git$/i, '')}`
  } catch {
    return url
  }
}

/** Group live environments by promotion lineage; stages ordered Preview → Staging → Production. */
export function groupEnvironmentsByLineage(
  environments: Environment[],
): EnvironmentLineageGroup[] {
  const map = new Map<string, Environment[]>()
  for (const env of environments) {
    const key = environmentLineageKey(env)
    const bucket = map.get(key)
    if (bucket) bucket.push(env)
    else map.set(key, [env])
  }

  const groups: EnvironmentLineageGroup[] = []
  for (const [lineageId, envs] of map) {
    const sorted = [...envs].sort((a, b) => {
      const stageDiff = stageRank(a) - stageRank(b)
      if (stageDiff !== 0) return stageDiff
      return a.name.localeCompare(b.name)
    })
    groups.push({
      lineageId,
      title: groupTitle(sorted),
      subtitle: groupSubtitle(sorted),
      environments: sorted,
    })
  }

  // Latest group first, by creation date. updated_at churns on every status poll,
  // so it does not reflect which group is newest - use created_at.
  const groupCreatedAt = (g: EnvironmentLineageGroup): number =>
    Math.max(
      0,
      ...g.environments.map((e) => new Date(e.created_at).getTime() || 0),
    )
  groups.sort((a, b) => groupCreatedAt(b) - groupCreatedAt(a))

  return groups
}

export function stageLabelKey(stage: LifecycleStage | string | null | undefined): string {
  const s = String(stage || 'preview').toLowerCase()
  if (s === 'staging') return 'environments.lifecycle.staging'
  if (s === 'production') return 'environments.lifecycle.production'
  return 'environments.lifecycle.preview'
}
