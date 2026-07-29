export type CloudProvider = 'local' | 'gcp' | 'aws' | 'azure' | 'cloudflare'
export type IaCEngine = 'terraform' | 'opentofu' | 'pulumi'
export type KubernetesPackaging = 'none' | 'raw_manifests' | 'helm'
export type WorkspaceArtifactsMode = 'iac_only' | 'manifest_only' | 'both'
export type ProvisionEngine = IaCEngine
export type K8sScaffoldMode = 'k8s' | 'helm'
export type CicdPlatform = 'github' | 'gitlab'

/** Container scan severity gate (Solution A). */
export type ScanSeverityThreshold = 'critical' | 'critical_high'

/** What to do when CVE findings exceed the threshold (Solution A). */
export type ScanFindingAction = 'block' | 'warn'

/** CodeQL language packs (Solution B / SAST). */
export type SastLanguage =
  | 'javascript-typescript'
  | 'python'
  | 'go'
  | 'java-kotlin'
  | 'csharp'
  | 'ruby'

export interface CicdContainerScanConfig {
  enabled: boolean
  severityThreshold: ScanSeverityThreshold
  onFinding: ScanFindingAction
}

export interface CicdSastGuardrailsConfig {
  enabled: boolean
  /** Run CodeQL / Semgrep static analysis before image build. */
  enableSast: boolean
  /** kubectl rollout status + auto undo on failure. */
  enableHealthRollback: boolean
  sastLanguages: SastLanguage[]
}

export interface CicdSecurityConfig {
  containerScan: CicdContainerScanConfig
  sastGuardrails: CicdSastGuardrailsConfig
}

/** Spot / preemptible placement strategy. */
export type SpotWorkloadPlacement = 'stateless_nonprod' | 'production_ondemand_fallback'

/** Node scaler strategy for spot capacity. */
export type SpotProvisionerStrategy = 'karpenter' | 'cluster_autoscaler'

/** Right-sizing presets for container requests/limits. */
export type ResourceSizingPreset = 'developer' | 'balanced' | 'performance' | 'custom'

/** Idle shutdown window — scale to 0 outside business hours. */
export type IdleShutdownSchedule = 'weeknights_weekends'

export interface SpotSchedulingConfig {
  enabled: boolean
  placement: SpotWorkloadPlacement
  allocationPercent: number
  provisioner: SpotProvisionerStrategy
}

export interface CostHpaConfig {
  enabled: boolean
  minReplicas: number
  maxReplicas: number
  targetCpuUtilization: number
}

export interface CostVpaConfig {
  /** Recommendation-only (updateMode Off) — no automatic pod restarts. */
  enabled: boolean
}

export interface CostResourceConfig {
  preset: ResourceSizingPreset
  cpuRequest: string
  cpuLimit: string
  memoryRequest: string
  memoryLimit: string
}

export interface IdleShutdownConfig {
  enabled: boolean
  schedule: IdleShutdownSchedule
}

export interface CostOptimizationConfig {
  spotScheduling: SpotSchedulingConfig
  hpa: CostHpaConfig
  vpa: CostVpaConfig
  resources: CostResourceConfig
  idleShutdown: IdleShutdownConfig
}

export interface InfraGenerationConfig {
  provision: { enabled: boolean; engine: ProvisionEngine }
  kubernetes: { enabled: boolean; mode: K8sScaffoldMode }
  cicd: {
    enabled: boolean
    platform: CicdPlatform
    security: CicdSecurityConfig
  }
}
export type IngressClassName =
  | 'nginx'
  | 'traefik'
  | 'gce'
  | 'alb'
  | 'azure-application-gateway'
  | 'contour'

export interface KubernetesWorkloadOptions {
  deployment: boolean
  service: boolean
  pod: boolean
  job: boolean
  cronjob: boolean
  statefulset: boolean
  daemonset: boolean
  ingress: boolean
  ingress_class: IngressClassName
  install_ingress_nginx: boolean
  config_map: boolean
  secret: boolean
  service_account: boolean
  pvc: boolean
  role: boolean
  role_binding: boolean
  hpa: boolean
  vpa: boolean
  pdb: boolean
  network_policy: boolean
  resource_quota: boolean
  limit_range: boolean
}

export interface IaCBundleSummary {
  workspace_id: string
  engine: IaCEngine
  provider: CloudProvider
  root_dir: string
  files: string[]
  artifact_mode?: WorkspaceArtifactsMode | null
  name?: string | null
  status?: string | null
  created_at?: string | null
}

export interface WorkspaceListItem {
  id: string
  name: string
  engine: string
  provider: string
  status: string
  artifact_mode: WorkspaceArtifactsMode
  created_at: string
  root_dir: string
}

export interface WorkspaceWizardConfig {
  name: string
  iac_engine: IaCEngine
  cloud:
    | { provider: 'local'; resources: Record<string, unknown> }
    | { provider: 'gcp'; resources: Record<string, unknown> }
    | { provider: 'aws'; resources: Record<string, unknown> }
    | { provider: 'azure'; resources: Record<string, unknown> }
    | { provider: 'cloudflare'; resources: Record<string, unknown> }
  run_init: boolean
  artifact_mode: WorkspaceArtifactsMode
  kubernetes_packaging: KubernetesPackaging
  kubernetes_options: KubernetesWorkloadOptions
  cost_optimization: CostOptimizationConfig
  has_credentials: boolean
}

export interface WorkspaceFileNode {
  path: string
  type: 'file' | 'directory'
  size: number | null
}

export interface WorkspaceFileContent {
  path: string
  content: string
}

export interface WorkspaceTemplateInfo {
  id: string
  label: string
  category: string
  description: string
  default_path: string
}

export interface WorkspacePushRequest {
  installation_id: number
  existing_full_name: string
  commit_message?: string
  include_workflow?: boolean
  include_dockerfiles?: boolean
}

export interface TerminalSessionResponse {
  session_id: string
  workspace_id: string
  mode: string
  ws_path: string
}

export interface GitHubRepoResult {
  full_name: string
  html_url: string
  private: boolean
  default_branch: string
  secrets_set: string[]
  workflow_path?: string | null
  installation_id?: number | null
  auth_method?: string
  created?: boolean
}

export interface GitHubInstallationItem {
  id: number
  account_login: string
  account_type: string
  target_type?: string | null
  repository_selection?: string | null
}

export interface GitHubRepositoryItem {
  id: number
  name: string
  full_name: string
  private: boolean
  html_url: string
  default_branch: string
  owner_login: string
}

export type FrameworkOption =
  | 'react_vite'
  | 'nextjs'
  | 'nuxtjs'
  | 'vuejs'
  | 'svelte'
  | 'fastapi'
  | 'flask'
  | 'django'
  | 'express'
  | 'nestjs'
  | 'springboot'
  | 'go'
  | 'rust'
  | 'node'
  | 'python'
  | 'java'
  | 'generic'

export type ProjectStackOption = FrameworkOption

export interface FrameworkItem {
  id: FrameworkOption
  label: string
  category: 'frontend' | 'python' | 'node' | 'backend'
}

export const FRAMEWORK_OPTIONS: FrameworkItem[] = [
  { id: 'react_vite', label: 'React (Vite)', category: 'frontend' },
  { id: 'nextjs', label: 'Next.js (SSR / Standalone)', category: 'frontend' },
  { id: 'nuxtjs', label: 'Nuxt.js (Nitro / Vue 3)', category: 'frontend' },
  { id: 'vuejs', label: 'Vue.js (Vite)', category: 'frontend' },
  { id: 'svelte', label: 'Svelte / SvelteKit', category: 'frontend' },
  { id: 'fastapi', label: 'FastAPI (Python)', category: 'python' },
  { id: 'flask', label: 'Flask (Python)', category: 'python' },
  { id: 'django', label: 'Django (Python)', category: 'python' },
  { id: 'express', label: 'Express.js (Node)', category: 'node' },
  { id: 'nestjs', label: 'NestJS (TypeScript)', category: 'node' },
  { id: 'springboot', label: 'Java (Spring Boot)', category: 'backend' },
  { id: 'go', label: 'Go (Golang)', category: 'backend' },
  { id: 'rust', label: 'Rust (Actix/Axum)', category: 'backend' },
  { id: 'generic', label: 'Generic (Alpine)', category: 'backend' },
]

export interface ContainerScaffoldConfig {
  enabled: boolean
  generate_dockerfile: boolean
  generate_docker_compose: boolean
  stack: ProjectStackOption
  frameworks?: FrameworkOption[]
  app_name: string
  listen_port: number
}

export interface GitHubRepositorySearchItem {
  id: number
  name: string
  fullName: string
  isPrivate: boolean
  owner: string
  defaultBranch: string
  htmlUrl: string
}

export interface GitHubRepositorySearchResponse {
  repositories: GitHubRepositorySearchItem[]
}

export interface GitHubAppStatus {
  configured: boolean
  app_id?: number | null
  app_slug?: string | null
  install_url?: string | null
  default_installation_id?: number | null
  message: string
  installations: GitHubInstallationItem[]
}

/** Result of docker image EXPOSE inspection (for containerPort / targetPort prefill). */
export interface ImageInspectResult {
  image: string
  exposed_ports: number[]
  listen_port: number
}

export type TerminalServerMessage =
  | { type: 'ready'; session_id: string; mode: string; cols: number; rows: number }
  | { type: 'output'; data: string }
  | { type: 'status'; status: string }
  | { type: 'error'; message: string; details?: unknown }
