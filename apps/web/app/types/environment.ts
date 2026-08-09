export type EnvironmentStatus =
  | 'PROVISIONING'
  | 'RUNNING'
  | 'PAUSED'
  | 'EXPIRED'
  | 'TEARDOWN_PENDING'
  | 'DESTROYED'
  | 'FAILED'

export type LogLevel = 'INFO' | 'WARN' | 'ERROR'

export type EnvStreamEventType = 'STATUS_CHANGE' | 'LOG' | 'EXECUTION_FAILED'

export interface PreviewEndpoint {
  name: string
  app_kind: 'frontend' | 'backend' | string
  url: string
  port?: number | null
  exposed?: boolean
}

export interface EnvStreamEvent {
  type: EnvStreamEventType
  status?: string | null
  commit_sha?: string | null
  message?: string | null
  log_level?: string | null
  environment_id?: string | null
  stage?: string | null
  timestamp?: string | null
  preview_url?: string | null
  node_port?: number | null
  app_ready?: boolean | null
  notice?: string | null
  error_message?: string | null
  preview_endpoints?: PreviewEndpoint[] | null
}

export interface Environment {
  id: string
  owner_id: string
  workspace_id: string | null
  name: string
  git_branch: string
  git_repo_url: string
  latest_commit_sha: string | null
  status: EnvironmentStatus
  namespace_name: string
  preview_url: string | null
  preview_endpoints_json?: string | null
  preview_endpoints?: PreviewEndpoint[]
  template_id: string | null
  provider?: string | null
  workload_image?: string | null
  node_port?: number | null
  github_pr_number?: number | null
  github_pr_url?: string | null
  stable_pr_url?: string | null
  ttl_expires_at: string
  cost_estimate_hourly: string
  cost_accrued: string
  cost_sampled_at?: string | null
  cost_source?: string | null
  time_remaining_seconds: number
  error_message: string | null
  created_at: string
  updated_at: string
  portal_url?: string | null
  gitops_rebuild_enabled?: boolean
  app_ready?: boolean
  ttl_warning?: boolean
  is_local?: boolean
  soft_cost_cap_exceeded?: boolean
  concurrent_active_count?: number | null
  max_concurrent_environments?: number | null
  runtime_summary?: string | null
  deploy_mode?: 'preview' | 'manifest' | 'compose' | 'attach'
  manifest_packaging?: string | null
  enable_postgres?: boolean
  enable_redis?: boolean
  drift_detected?: boolean
  drift_summary?: string | null
}

export interface DeploymentLog {
  id: string
  environment_id: string
  log_level: LogLevel
  message: string
  timestamp: string
}

export interface AuditLogEntry {
  id: string
  workspace_id: string | null
  environment_id: string | null
  actor_id: string
  action: string
  commit_sha: string | null
  status: string
  detail: string | null
  timestamp: string
}

export interface KindClusterStatus {
  status: string
  cluster: string
  context: string
  kind_installed: boolean
  kubectl_installed: boolean
  cluster_exists: boolean
  api_reachable: boolean
  auto_manage: boolean
  message: string
  can_launch: boolean
}

export interface PreviewBuildStatus {
  enabled: boolean
  dockerfile: string
  kind_load: boolean
  registry: string | null
  message: string
}

export interface EnvironmentCreatePayload {
  name: string
  git_branch: string
  git_repo_url: string
  ttl_hours?: number | null
  ttl_minutes?: number | null
  workspace_id?: string | null
  template_id?: string | null
}

export interface PreviewAppTemplate {
  id: string
  title: string
  description: string
  icon: string
  git_repo_url: string
  git_branch: string
  default_ttl_hours: number
  hourly_cost_hint: string
  workload_image: string
  tags: string[]
  enable_postgres?: boolean
  enable_redis?: boolean
}

export interface PreviewLaunchPayload {
  name: string
  template_id?: string | null
  git_repo_url?: string | null
  git_branch?: string | null
  workload_image?: string | null
  provider: 'local' | 'gcp' | 'aws' | 'azure' | 'cloudflare'
  credentials?: Record<string, string | null | undefined>
  ttl_hours?: number | null
  ttl_minutes?: number | null
  workspace_id?: string | null
  github_pr_number?: number | null
  github_pr_url?: string | null
  enable_postgres?: boolean
  enable_redis?: boolean
  deploy_mode?: 'preview' | 'manifest' | 'compose' | 'attach'
}

export interface EnvironmentExtendPayload {
  hours?: number | null
  minutes?: number | null
}

export interface EnvironmentPromotePayload {
  provider: 'gcp' | 'aws' | 'azure' | 'cloudflare'
  credentials?: Record<string, string | null | undefined>
  name?: string | null
  ttl_hours?: number | null
  ttl_minutes?: number | null
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    correlation_id?: string | null
    details?: Record<string, unknown> | null
  }
}
