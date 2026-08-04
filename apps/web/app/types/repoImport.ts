/** Repository import → detect → save workspace types. */

export type ServiceRole = 'web' | 'api' | 'worker' | 'unknown'
export type ProjectLayout = 'monorepo' | 'single'
export type MonorepoTool =
  | 'pnpm'
  | 'lerna'
  | 'turbo'
  | 'nx'
  | 'npm_workspaces'
  | 'cargo'
  | 'go_work'
  | 'make'
  | 'none'

export interface DetectedService {
  id: string
  name: string
  path: string
  role: ServiceRole
  framework: string
  runtime: string
  port: number
  has_dockerfile: boolean
  dockerfile_path: string | null
  env_hints: Record<string, string>
  enabled: boolean
  is_preview_target: boolean
  health_path: string
  markers: string[]
}

export interface DetectionResult {
  layout: ProjectLayout
  monorepo_tools: MonorepoTool[]
  services: DetectedService[]
  datastores: string[]
  root_markers: string[]
  package_globs: string[]
  summary: string
}

export interface RepoImportSession {
  import_id: string
  git_repo_url: string
  git_branch: string
  commit_sha: string
  layout: ProjectLayout
  detection: DetectionResult
  services: DetectedService[]
  created_at: string | null
}

export interface ServiceOverride {
  id: string
  enabled: boolean
  port?: number | null
  is_preview_target: boolean
  name?: string | null
}

export interface RepoImportSaveResult {
  workspace_id: string
  name: string
  root_dir: string
  files: string[]
  preview_service: string | null
  cluster_ready: boolean
  message: string
}
