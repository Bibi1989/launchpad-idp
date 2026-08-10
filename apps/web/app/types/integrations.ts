export interface SlackIntegrationStatus {
  connected: boolean
  notify_ready: boolean
  notify_failed: boolean
  notify_ttl_warning: boolean
  notify_cost_cap: boolean
  project_ids: string[]
  webhook_configured: boolean
  updated_at?: string | null
}

export interface SlackIntegrationUpdate {
  webhook_url?: string | null
  notify_ready?: boolean
  notify_failed?: boolean
  notify_ttl_warning?: boolean
  notify_cost_cap?: boolean
  project_ids?: string[]
  clear_webhook?: boolean
}

export interface JiraIntegrationStatus {
  connected: boolean
  site_url?: string | null
  email?: string | null
  project_key?: string | null
  issue_type: string
  auto_create_on_failure: boolean
  token_configured: boolean
  updated_at?: string | null
}

export interface JiraIntegrationUpdate {
  site_url?: string | null
  email?: string | null
  api_token?: string | null
  project_key?: string | null
  issue_type?: string | null
  auto_create_on_failure?: boolean
  clear?: boolean
}

export interface JiraIssueRead {
  issue_key: string
  issue_url: string
  created: boolean
}
