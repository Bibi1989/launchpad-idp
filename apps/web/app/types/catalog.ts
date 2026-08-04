export type ServiceTier = 'critical' | 'tier-1' | 'tier-2' | 'tier-3'

export interface GoldenPathTemplate {
  id: string
  version: string
  title: string
  description: string
  icon: string
  stack: string
  frameworks: string[]
  docker_images: string[]
  default_tier: ServiceTier
  default_slo: string
  listen_port: number
  tags: string[]
  includes_dockerfile: boolean
  includes_k8s: boolean
  includes_cicd: boolean
  includes_iac: boolean
  enable_postgres?: boolean
  enable_redis?: boolean
}

export interface ScorecardItem {
  id: string
  title: string
  passed: boolean
  points: number
  max_points: number
  detail: string
}

export interface ServiceScorecard {
  score: number
  gate: number
  passed: boolean
  items: ScorecardItem[]
}

export interface CatalogService {
  id: string
  name: string
  description: string
  owner: string
  tier: ServiceTier
  slo_target: string
  runbook_url: string | null
  on_call: string | null
  template_id: string
  template_version: string
  repository_url: string | null
  workspace_id: string | null
  compliance_score: number
  scorecard: ServiceScorecard
  org_id: string | null
  initial_preview_id?: string | null
  initial_preview_url?: string | null
  created_at: string
  updated_at: string
}

export interface CatalogServiceCreatePayload {
  name: string
  description?: string
  template_id: string
  owner: string
  tier?: ServiceTier
  slo_target?: string
  runbook_url?: string | null
  on_call?: string | null
  vcs_provider?: 'none' | 'github' | 'gitlab'
  create_github_repo?: boolean
  github_installation_id?: number | null
  github_organization?: string | null
  github_private?: boolean
  gitlab_project_name?: string | null
  gitlab_private?: boolean
  enforce_scorecard_gate?: boolean
  trigger_initial_preview?: boolean
}

export interface CatalogServiceUpdatePayload {
  description?: string
  owner?: string
  tier?: ServiceTier
  slo_target?: string
  runbook_url?: string | null
  on_call?: string | null
}
