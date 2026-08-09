export type CloudProvider = 'local' | 'gcp' | 'aws' | 'azure' | 'cloudflare'
export type IaCEngine = 'terraform' | 'opentofu' | 'pulumi' | 'ansible'

export type AnsibleAppDeployMode = 'docker_run' | 'docker_compose' | 'systemd' | 'none'

export interface AnsibleConfig {
  enabled: boolean
  hosts: string
  inventory_group: string
  ssh_user: string
  ssh_port: number
  ssh_private_key_path?: string | null
  become: boolean
  become_user: string
  python_interpreter: string
  set_hostname: boolean
  hostname?: string | null
  timezone: string
  packages: string[]
  install_docker: boolean
  install_compose_plugin: boolean
  enable_ufw: boolean
  ufw_allow_ports: number[]
  enable_fail2ban: boolean
  enable_unattended_upgrades: boolean
  create_deploy_user: boolean
  deploy_user: string
  deploy_user_groups: string[]
  app_deploy_mode: AnsibleAppDeployMode
  app_dir: string
  app_listen_port: number
  sync_workspace: boolean
  use_vault: boolean
  vault_password_file?: string | null
}
export type KubernetesPackaging = 'none' | 'raw_manifests' | 'helm' | 'kustomize'
export type WorkspaceArtifactsMode = 'iac_only' | 'manifest_only' | 'both'
export type WorkspaceRuntimeMode = 'kubernetes' | 'docker_compose' | 'running_instance'
export type RunningInstanceKind = 'serverless' | 'vm' | 'local_machine'
export type ProvisionEngine = IaCEngine
export type K8sScaffoldMode = 'k8s' | 'helm' | 'kustomize'
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

/** Pinned container CVE scanner (GitHub Action or OCI image). */
export type ContainerScanToolId =
  | 'trivy-action-v0.30.0'
  | 'trivy-0.58.1'
  | 'trivy-0.57.2'
  | 'trivy-0.56.2'

/** Pinned SAST scanner (CodeQL actions or Semgrep image). */
export type SastToolId =
  | 'codeql-v3.28.10'
  | 'semgrep-1.97.0'
  | 'semgrep-1.96.0'
  | 'semgrep-1.95.0'

export interface CicdContainerScanConfig {
  enabled: boolean
  severityThreshold: ScanSeverityThreshold
  onFinding: ScanFindingAction
  tool: ContainerScanToolId
}

export interface CicdSastGuardrailsConfig {
  enabled: boolean
  /** Run CodeQL / Semgrep static analysis before image build. */
  enableSast: boolean
  /** kubectl rollout status + auto undo on failure. */
  enableHealthRollback: boolean
  sastLanguages: SastLanguage[]
  sastTool: SastToolId
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

/** Idle shutdown window - scale to 0 outside business hours. */
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
  /** Recommendation-only (updateMode Off) - no automatic pod restarts. */
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
    /** Target frameworks for per-service CI workflows (Nuxt / FastAPI / NestJS, …). */
    frameworks: FrameworkOption[]
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

export type DependencyPlacement = 'in_cluster' | 'managed'

export type DataStoreKind = 'postgres' | 'mysql' | 'mongodb' | 'redis'

export interface DataStoreDependency {
  enabled: boolean
  placement: DependencyPlacement
}

export interface WorkloadDependenciesConfig {
  postgres: DataStoreDependency
  mysql: DataStoreDependency
  mongodb: DataStoreDependency
  redis: DataStoreDependency
}

export interface RunningInstanceConfig {
  kind: RunningInstanceKind
  service_name?: string | null
  region?: string | null
  host?: string | null
  ssh_user?: string | null
  ssh_port?: number
  ssh_key_path?: string | null
  listen_port?: number
  preview_url_override?: string | null
  /** @deprecated coerced from older snapshots */
  kube_context?: string | null
  /** @deprecated use preview_url_override */
  endpoint_url?: string | null
}

export interface IaCBundleSummary {
  workspace_id: string
  engine: IaCEngine
  provider: CloudProvider
  root_dir: string
  files: string[]
  artifact_mode?: WorkspaceArtifactsMode | null
  runtime_mode?: WorkspaceRuntimeMode | null
  name?: string | null
  status?: string | null
  created_at?: string | null
  starred?: boolean
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
  starred: boolean
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
  runtime_mode: WorkspaceRuntimeMode
  running_instance: RunningInstanceConfig
  artifact_mode: WorkspaceArtifactsMode
  kubernetes_packaging: KubernetesPackaging
  kubernetes_options: KubernetesWorkloadOptions
  cost_optimization: CostOptimizationConfig
  container_scaffold: ContainerScaffoldConfig
  dependencies: WorkloadDependenciesConfig
  ansible: AnsibleConfig
  has_credentials: boolean
  /** Safe display name for the stored cloud key (never the secret). */
  credential_label?: string | null
}

export interface GcpApiEnablementResult {
  project_id: string
  required: string[]
  already_enabled: string[]
  newly_enabled: string[]
  waited_seconds: number
  message: string
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

export interface GitlabStatus {
  connected: boolean
  oauth_configured: boolean
  authorize_url: string | null
  base_url: string
  username: string | null
  token_type: string | null
  message: string
}

export interface GitlabProjectItem {
  id: number
  name: string
  path_with_namespace: string
  http_url_to_repo: string
  web_url: string
  visibility: string
  default_branch: string
}

export interface GitlabRepoResult {
  id: number
  path_with_namespace: string
  web_url: string
  http_url_to_repo: string
  default_branch: string
  visibility: string
  created: boolean
  files_committed: number
}

export interface GitlabPushRequest {
  project_path: string
  commit_message?: string
}

export interface GitlabRepoInput {
  name: string
  description?: string
  private?: boolean
  workspace_id?: string | null
  existing_path?: string | null
  include_ci?: boolean
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

export interface ContainerServiceItem {
  name: string
  stack: ProjectStackOption
  app_kind?: 'frontend' | 'backend'
  listen_port: number
  dockerfile_path?: string | null
  /** When true, publish host port and treat as Open-app / browser target. */
  expose_preview?: boolean | null
}

export interface ContainerScaffoldConfig {
  enabled: boolean
  generate_dockerfile: boolean
  generate_docker_compose: boolean
  stack: ProjectStackOption
  frameworks: FrameworkOption[]
  app_name: string
  listen_port: number
  services?: ContainerServiceItem[]
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
