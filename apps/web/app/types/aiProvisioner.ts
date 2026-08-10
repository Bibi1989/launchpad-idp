// Types mirroring the AI Infrastructure Provisioner API (app/schemas/ai_provisioner.py).

export type BlueprintTarget = 'local_node' | 'gcp' | 'aws' | 'azure'
export type ServiceKind = 'web' | 'worker' | 'datastore' | 'cache'
export type GuardrailSeverity = 'error' | 'warning'

export interface BlueprintPort {
  container_port: number
  host_port: number
  protocol?: 'tcp' | 'udp'
}

export interface BlueprintVolume {
  host_path: string
  container_path: string
  mode?: 'rw' | 'ro'
}

export interface InfraServiceSpec {
  name: string
  image: string
  kind: ServiceKind
  ports: BlueprintPort[]
  env: Record<string, string>
  volumes: BlueprintVolume[]
  cpu_limit: number
  memory_mb: number
  replicas: number
  persistent: boolean
  command?: string | null
}

export interface InfraBlueprint {
  name: string
  summary: string
  services: InfraServiceSpec[]
  notes: string[]
}

export interface GuardrailViolation {
  code: string
  message: string
  severity: GuardrailSeverity
  service?: string | null
}

export interface BlueprintValidation {
  valid: boolean
  adjusted: boolean
  violations: GuardrailViolation[]
}

export interface CostLineItem {
  service: string
  cpu_usd: number
  memory_usd: number
  addon_usd: number
  hourly_usd: number
}

export interface CostEstimate {
  hourly_usd: number
  monthly_usd: number
  currency: string
  self_hosted: boolean
  breakdown: CostLineItem[]
}

export interface BlueprintGenerateRequest {
  prompt: string
  target: BlueprintTarget
  node_id?: string | null
  region?: string | null
}

export interface BlueprintFixRequest {
  blueprint: InfraBlueprint
  error_log: string
  prompt?: string | null
  target: BlueprintTarget
  node_id?: string | null
  region?: string | null
}

export interface BlueprintGenerateResponse {
  blueprint: InfraBlueprint
  target: BlueprintTarget
  node_id: string | null
  source: string
  validation: BlueprintValidation
  cost: CostEstimate
}

export interface BlueprintDeployRequest {
  blueprint: InfraBlueprint
  target: BlueprintTarget
  node_id?: string | null
  region?: string | null
}

export interface DeployStepResult {
  step: string
  ok: boolean
  detail: string
}

export interface DeployedServiceLink {
  name: string
  container_name: string
  host_port: number | null
  container_port: number | null
  url: string | null
  ok: boolean
}

export interface BlueprintDeployResponse {
  deployment_id: string
  target: BlueprintTarget
  mode: string
  node_id: string | null
  node_name: string | null
  ok: boolean
  steps: DeployStepResult[]
  logs: string[]
  workspace_id: string | null
  view_path: string | null
  services: DeployedServiceLink[]
}

export interface AiProvisionerStatus {
  gemini_configured: boolean
  model: string
  heuristic_fallback: boolean
}
