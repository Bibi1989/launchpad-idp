export interface OrgSummary {
  id: string
  slug: string
  name: string
  role: string
}

export interface AuthUser {
  id: string
  email: string
  display_name: string
}

export interface AuthConfig {
  dev_login_enabled: boolean
  oidc_enabled?: boolean
  oidc_provider_name?: string | null
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: AuthUser
  orgs?: OrgSummary[]
  active_org_id?: string | null
}

export interface MeResponse {
  user: AuthUser
  orgs: OrgSummary[]
  active_org_id: string | null
}

export interface OrgCostEnvironmentItem {
  environment_id: string
  name: string
  status: string
  provider: string | null
  is_local: boolean
  cost_estimate_hourly: string
  cost_accrued: string
}

export interface OrgCostSummary {
  org_id: string
  soft_cost_cap: string
  active_count: number
  cloud_environment_count: number
  cloud_accrued: string
  local_accrued: string
  total_accrued: string
  soft_cost_cap_exceeded: boolean
  environments: OrgCostEnvironmentItem[]
}

export interface OrgMember {
  user_id: string
  email: string
  display_name: string
  role: string
}

export interface OrgInvite {
  id: string
  org_id: string
  org_name?: string | null
  email: string
  role: string
  expires_at: string
  accepted_at?: string | null
  revoked_at?: string | null
  created_at: string
  invite_url?: string | null
  email_sent?: boolean
}

export interface OrgSsoMapping {
  id: string
  org_id: string
  group_name: string
  role: string
  created_at: string
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload {
  email: string
  password: string
  display_name: string
}
