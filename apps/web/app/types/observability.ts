export interface EnvironmentMetrics {
  environment_id: string
  name: string
  status: string
  namespace_name: string
  cpu_cores: number
  memory_gib: number
  cpu_percent: number | null
  memory_percent: number | null
  source: string | null
  available: boolean
  detail: string | null
  sampled_at: string
}

export interface EnvironmentHealthPing {
  environment_id: string
  name: string
  status: string
  ok: boolean
  status_code: number | null
  message: string
  preview_url: string | null
  latency_ms: number | null
  checked_at: string
}

export interface EnvironmentObservabilityItem {
  environment_id: string
  name: string
  status: string
  provider: string | null
  deploy_mode: string | null
  app_ready: boolean
  preview_url: string | null
  metrics: EnvironmentMetrics
  health: EnvironmentHealthPing
}

export interface EnvironmentObservabilitySummary {
  items: EnvironmentObservabilityItem[]
  healthy_count: number
  unhealthy_count: number
  unknown_count: number
  sampled_at: string
}
