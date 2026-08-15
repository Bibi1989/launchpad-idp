import { z } from 'zod'

export const cloudProviderSchema = z.enum(['local', 'gcp', 'aws', 'azure', 'cloudflare'])
export const iacEngineSchema = z.enum(['terraform', 'opentofu', 'pulumi', 'ansible'])

export const ansibleConfigSchema = z.object({
  enabled: z.boolean().default(false),
  hosts: z.string().trim().min(1).max(2048).default('127.0.0.1'),
  inventory_group: z.string().trim().min(1).max(64).default('app_servers'),
  ssh_user: z.string().trim().min(1).max(64).default('ubuntu'),
  ssh_port: z.number().int().min(1).max(65535).default(22),
  ssh_private_key_path: z.string().trim().max(512).nullable().optional(),
  become: z.boolean().default(true),
  become_user: z.string().trim().min(1).max(64).default('root'),
  python_interpreter: z.string().trim().min(1).max(128).default('auto'),
  set_hostname: z.boolean().default(true),
  hostname: z.string().trim().max(253).nullable().optional(),
  timezone: z.string().trim().min(1).max(64).default('UTC'),
  packages: z.array(z.string().trim().min(1)).default(['curl', 'ca-certificates', 'gnupg', 'jq', 'htop']),
  install_docker: z.boolean().default(true),
  install_compose_plugin: z.boolean().default(true),
  enable_ufw: z.boolean().default(true),
  ufw_allow_ports: z.array(z.number().int().min(1).max(65535)).default([22, 80, 443]),
  enable_fail2ban: z.boolean().default(true),
  enable_unattended_upgrades: z.boolean().default(true),
  create_deploy_user: z.boolean().default(true),
  deploy_user: z.string().trim().min(1).max(64).default('deploy'),
  deploy_user_groups: z.array(z.string().trim().min(1)).default(['docker']),
  app_deploy_mode: z.enum(['docker_run', 'docker_compose', 'systemd', 'pm2', 'none']).default('docker_run'),
  app_dir: z.string().trim().min(1).max(512).default('/opt/launchpad/app'),
  app_listen_port: z.number().int().min(1).max(65535).default(8080),
  reverse_proxy: z.enum(['none', 'nginx', 'caddy']).default('none'),
  app_start_command: z.string().trim().max(512).nullable().optional(),
  sync_workspace: z.boolean().default(true),
  use_vault: z.boolean().default(false),
  vault_password_file: z.string().trim().max(512).nullable().optional(),
})

export const defaultAnsibleConfig = (): z.infer<typeof ansibleConfigSchema> => ({
  enabled: false,
  hosts: '127.0.0.1',
  inventory_group: 'app_servers',
  ssh_user: 'ubuntu',
  ssh_port: 22,
  ssh_private_key_path: '~/.ssh/id_ed25519',
  become: true,
  become_user: 'root',
  python_interpreter: 'auto',
  set_hostname: true,
  hostname: null,
  timezone: 'UTC',
  packages: ['curl', 'ca-certificates', 'gnupg', 'jq', 'htop'],
  install_docker: true,
  install_compose_plugin: true,
  enable_ufw: true,
  ufw_allow_ports: [22, 80, 443],
  enable_fail2ban: true,
  enable_unattended_upgrades: true,
  create_deploy_user: true,
  deploy_user: 'deploy',
  deploy_user_groups: ['docker'],
  app_deploy_mode: 'docker_run',
  app_dir: '/opt/launchpad/app',
  app_listen_port: 8080,
  reverse_proxy: 'none',
  app_start_command: null,
  sync_workspace: true,
  use_vault: false,
  vault_password_file: null,
})
export const secretBackendSchema = z.enum(['secret_manager', 'native_k8s'])
export const kubernetesPackagingSchema = z.enum(['none', 'raw_manifests', 'helm', 'kustomize'])
export const workspaceArtifactsModeSchema = z.enum(['iac_only', 'manifest_only', 'both'])
export const workspaceRuntimeModeSchema = z.enum([
  'kubernetes',
  'docker_compose',
  'running_instance',
])
export const runningInstanceKindSchema = z.enum([
  'serverless',
  'vm',
  'local_machine',
])
export const runningInstanceSchema = z.object({
  kind: runningInstanceKindSchema.default('local_machine'),
  service_name: z.string().max(63).nullable().optional(),
  region: z.string().max(64).nullable().optional(),
  host: z.string().max(255).nullable().optional(),
  ssh_user: z.string().max(64).nullable().optional(),
  ssh_port: z.number().int().min(1).max(65535).default(22),
  ssh_key_path: z.string().max(512).nullable().optional(),
  listen_port: z.number().int().min(1).max(65535).default(8080),
  process_strategy: z.enum(['docker', 'systemd', 'pm2']).default('docker'),
  code_source: z.enum(['ssh', 'github']).default('ssh'),
  reverse_proxy: z.enum(['none', 'nginx', 'caddy']).default('none'),
  preview_url_override: z.string().max(512).nullable().optional(),
  kube_context: z.string().max(128).nullable().optional(),
  endpoint_url: z.string().max(512).nullable().optional(),
})
export const ingressClassSchema = z.enum([
  'nginx',
  'traefik',
  'gce',
  'alb',
  'azure-application-gateway',
  'contour',
])

export const localResourcesSchema = z.object({
  cluster_name: z.string().min(1).max(64).default('launchpad'),
  context: z.string().min(1).max(128).default('kind-launchpad'),
})

export const costOptimizationSchema = z.object({
  spotScheduling: z.object({
    enabled: z.boolean().default(false),
    placement: z.enum(['stateless_nonprod', 'production_ondemand_fallback']).default('stateless_nonprod'),
    allocationPercent: z.number().int().min(0).max(100).default(80),
    provisioner: z.enum(['karpenter', 'cluster_autoscaler']).default('karpenter'),
  }),
  hpa: z.object({
    enabled: z.boolean().default(false),
    minReplicas: z.number().int().min(1).max(100).default(2),
    maxReplicas: z.number().int().min(1).max(200).default(10),
    targetCpuUtilization: z.number().int().min(1).max(100).default(70),
  }).superRefine((value, ctx) => {
    if (value.maxReplicas < value.minReplicas) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Max replicas must be >= min replicas',
        path: ['maxReplicas'],
      })
    }
  }),
  vpa: z.object({
    enabled: z.boolean().default(false),
  }),
  resources: z.object({
    preset: z.enum(['developer', 'balanced', 'performance', 'custom']).default('developer'),
    cpuRequest: z.string().min(1).max(32).default('100m'),
    cpuLimit: z.string().min(1).max(32).default('250m'),
    memoryRequest: z.string().min(1).max(32).default('128Mi'),
    memoryLimit: z.string().min(1).max(32).default('256Mi'),
  }),
  idleShutdown: z.object({
    enabled: z.boolean().default(false),
    schedule: z.enum(['weeknights_weekends']).default('weeknights_weekends'),
  }),
})

export const defaultCostOptimization = (): z.infer<typeof costOptimizationSchema> => ({
  spotScheduling: {
    enabled: false,
    placement: 'stateless_nonprod',
    allocationPercent: 80,
    provisioner: 'karpenter',
  },
  hpa: {
    enabled: false,
    minReplicas: 2,
    maxReplicas: 10,
    targetCpuUtilization: 70,
  },
  vpa: { enabled: false },
  resources: {
    preset: 'developer',
    cpuRequest: '100m',
    cpuLimit: '250m',
    memoryRequest: '128Mi',
    memoryLimit: '256Mi',
  },
  idleShutdown: {
    enabled: false,
    schedule: 'weeknights_weekends',
  },
})

export const kubernetesImageSourceSchema = z.enum(['external', 'build_registry'])

export const kubernetesWorkloadOptionsSchema = z
  .object({
    deployment: z.boolean().default(true),
    service: z.boolean().default(true),
    pod: z.boolean().default(false),
    job: z.boolean().default(false),
    cronjob: z.boolean().default(false),
    statefulset: z.boolean().default(false),
    daemonset: z.boolean().default(false),
    ingress: z.boolean().default(false),
    ingress_class: ingressClassSchema.default('nginx'),
    install_ingress_nginx: z.boolean().default(false),
    config_map: z.boolean().default(false),
    secret: z.boolean().default(false),
    service_account: z.boolean().default(false),
    pvc: z.boolean().default(false),
    role: z.boolean().default(false),
    role_binding: z.boolean().default(false),
    hpa: z.boolean().default(false),
    vpa: z.boolean().default(false),
    pdb: z.boolean().default(false),
    network_policy: z.boolean().default(false),
    resource_quota: z.boolean().default(false),
    limit_range: z.boolean().default(false),
    image_source: kubernetesImageSourceSchema.default('build_registry'),
    image_scan: z
      .object({
        enabled: z.boolean().default(false),
        severity_threshold: z.enum(['critical', 'critical_high']).default('critical_high'),
        on_finding: z.enum(['block', 'warn']).default('block'),
        tool: z
          .enum(['trivy-action-v0.30.0', 'trivy-0.58.1', 'trivy-0.57.2', 'trivy-0.56.2'])
          .default('trivy-0.58.1'),
      })
      .default({
        enabled: false,
        severity_threshold: 'critical_high',
        on_finding: 'block',
        tool: 'trivy-0.58.1',
      }),
  })
  .superRefine((value, ctx) => {
    if (value.install_ingress_nginx && !value.ingress) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Enable Ingress before installing ingress-nginx',
        path: ['install_ingress_nginx'],
      })
    }
    if (value.install_ingress_nginx && value.ingress_class !== 'nginx') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'ingress-nginx install requires ingress class nginx',
        path: ['ingress_class'],
      })
    }
    const selected = [
      value.deployment,
      value.service,
      value.pod,
      value.job,
      value.cronjob,
      value.statefulset,
      value.daemonset,
      value.ingress,
      value.config_map,
      value.secret,
      value.service_account,
      value.pvc,
      value.role,
      value.role_binding,
      value.hpa,
      value.vpa,
      value.pdb,
      value.network_policy,
      value.resource_quota,
      value.limit_range,
    ]
    if (!selected.some(Boolean)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Select at least one Kubernetes object to scaffold',
        path: ['deployment'],
      })
    }
  })

export const defaultKubernetesWorkloadOptions = (): z.infer<
  typeof kubernetesWorkloadOptionsSchema
> => ({
  deployment: true,
  service: true,
  pod: false,
  job: false,
  cronjob: false,
  statefulset: false,
  daemonset: false,
  ingress: false,
  ingress_class: 'nginx',
  install_ingress_nginx: false,
  config_map: false,
  secret: false,
  service_account: false,
  pvc: false,
  role: false,
  role_binding: false,
  hpa: false,
  vpa: false,
  pdb: false,
  network_policy: false,
  resource_quota: false,
  limit_range: false,
  image_source: 'build_registry',
  image_scan: {
    enabled: false,
    severity_threshold: 'critical_high' as const,
    on_finding: 'block' as const,
    tool: 'trivy-0.58.1' as const,
  },
})

export function defaultImageSecurityScanConfig() {
  return {
    enabled: false,
    severity_threshold: 'critical_high' as const,
    on_finding: 'block' as const,
    tool: 'trivy-0.58.1' as const,
  }
}

export const networkTopologySchema = z.enum(['simple', 'standard'])

export const sqlDatabaseEngineSchema = z.enum(['postgres', 'mysql', 'mariadb'])
export const cacheEngineSchema = z.enum(['redis', 'memcached'])
export const cosmosApiKindSchema = z.enum(['mongodb', 'sql'])
export const lambdaRuntimeSchema = z.enum(['nodejs20.x', 'python3.12', 'provided.al2023'])

export const gcpResourcesSchema = z.object({
  vpc: z.boolean().default(true),
  subnets: z.boolean().default(true),
  existing_vpc_id: z.string().max(128).nullable().optional(),
  network_topology: networkTopologySchema.default('simple'),
  gke: z.boolean().default(false),
  artifact_registry: z.boolean().default(false),
  secret_backend: secretBackendSchema.default('secret_manager'),
  cloud_run: z.boolean().default(false),
  cloud_functions: z.boolean().default(false),
  cloud_sql: z.boolean().default(false),
  cloud_sql_engine: sqlDatabaseEngineSchema.default('postgres'),
  cloud_storage: z.boolean().default(false),
  pubsub: z.boolean().default(false),
  memorystore: z.boolean().default(false),
  memorystore_engine: cacheEngineSchema.default('redis'),
  bigquery: z.boolean().default(false),
  region: z.string().min(2).max(64).default('us-central1'),
  machine_type: z.string().min(3).max(64).default('e2-standard-4'),
  project_id: z
    .string()
    .trim()
    .min(3, 'GCP Project ID is required (at least 3 characters)')
    .max(64)
    .regex(/^[a-z][a-z0-9-]*$/, 'Use lowercase letters, numbers, and hyphens'),
}).superRefine((value, ctx) => {
  if (value.cloud_sql && value.cloud_sql_engine === 'mariadb') {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Cloud SQL supports postgres or mysql (not mariadb)',
      path: ['cloud_sql_engine'],
    })
  }
})

export const awsResourcesSchema = z.object({
  vpc: z.boolean().default(true),
  subnets: z.boolean().default(true),
  existing_vpc_id: z.string().max(128).nullable().optional(),
  existing_security_group_id: z.string().max(128).nullable().optional(),
  network_topology: networkTopologySchema.default('simple'),
  ec2: z.boolean().default(false),
  s3: z.boolean().default(false),
  eks: z.boolean().default(false),
  secrets_manager: z.boolean().default(true),
  rds: z.boolean().default(false),
  rds_engine: sqlDatabaseEngineSchema.default('postgres'),
  ecr: z.boolean().default(false),
  app_runner: z.boolean().default(false),
  elasticache: z.boolean().default(false),
  elasticache_engine: cacheEngineSchema.default('redis'),
  lambda_fn: z.boolean().default(false),
  lambda_runtime: lambdaRuntimeSchema.default('nodejs20.x'),
  dynamodb: z.boolean().default(false),
  sqs: z.boolean().default(false),
  alb: z.boolean().default(false),
  region: z.string().min(2).max(32).default('us-east-1'),
  instance_type: z.string().min(3).max(64).default('t3.medium'),
  account_alias: z.string().max(64).optional().nullable(),
})

export const azureResourcesSchema = z.object({
  vnet: z.boolean().default(true),
  subnets: z.boolean().default(true),
  network_topology: networkTopologySchema.default('simple'),
  aks: z.boolean().default(false),
  key_vault: z.boolean().default(true),
  container_apps: z.boolean().default(false),
  acr: z.boolean().default(false),
  storage_account: z.boolean().default(false),
  cosmos_db: z.boolean().default(false),
  cosmos_api: cosmosApiKindSchema.default('mongodb'),
  redis_cache: z.boolean().default(false),
  app_service: z.boolean().default(false),
  log_analytics: z.boolean().default(false),
  location: z.string().min(2).max(64).default('eastus'),
  vm_size: z.string().min(3).max(64).default('Standard_D2_v2'),
  resource_group: z
    .string()
    .trim()
    .min(3, 'Azure resource group is required (at least 3 characters)')
    .max(90)
    .regex(/^[-\w\._\(\)]+$/, 'Invalid Azure resource group name'),
})

export const cloudflareResourcesSchema = z
  .object({
    workers: z.boolean().default(false),
    r2: z.boolean().default(false),
    dns_records: z.boolean().default(false),
    pages: z.boolean().default(false),
    kv: z.boolean().default(false),
    d1: z.boolean().default(false),
    tunnels: z.boolean().default(false),
    queues: z.boolean().default(false),
    account_id: z.string().min(8).max(64),
    zone_name: z.string().max(253).optional().nullable(),
  })
  .superRefine((value, ctx) => {
    if (value.dns_records && !value.zone_name) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'zone_name is required when dns_records is enabled',
        path: ['zone_name'],
      })
    }
  })

export const cloudCredentialsSchema = z.object({
  gcp_sa_key_json: z.string().optional().nullable(),
  gcp_project_id: z.string().optional().nullable(),
  gcp_region: z.string().optional().nullable(),
  gcp_wif_project_number: z.string().optional().nullable(),
  gcp_wif_pool_id: z.string().optional().nullable(),
  gcp_wif_provider_id: z.string().optional().nullable(),
  gcp_wif_target_sa_email: z.string().optional().nullable(),
  aws_access_key_id: z.string().optional().nullable(),
  aws_secret_access_key: z.string().optional().nullable(),
  aws_session_token: z.string().optional().nullable(),
  aws_region: z.string().optional().nullable(),
  aws_role_arn: z.string().optional().nullable(),
  aws_role_session_name: z.string().optional().nullable(),
  azure_client_id: z.string().optional().nullable(),
  azure_client_secret: z.string().optional().nullable(),
  azure_tenant_id: z.string().optional().nullable(),
  azure_subscription_id: z.string().optional().nullable(),
  azure_location: z.string().optional().nullable(),
  cloudflare_api_token: z.string().optional().nullable(),
})

export type CloudCredentialsForm = {
  gcp_sa_key_json: string
  gcp_project_id: string
  gcp_region: string
  gcp_wif_project_number: string
  gcp_wif_pool_id: string
  gcp_wif_provider_id: string
  gcp_wif_target_sa_email: string
  aws_access_key_id: string
  aws_secret_access_key: string
  aws_session_token: string
  aws_region: string
  aws_role_arn: string
  aws_role_session_name: string
  azure_client_id: string
  azure_client_secret: string
  azure_tenant_id: string
  azure_subscription_id: string
  azure_location: string
  cloudflare_api_token: string
}

export const emptyCloudCredentials = (): CloudCredentialsForm => ({
  gcp_sa_key_json: '',
  gcp_project_id: '',
  gcp_region: '',
  gcp_wif_project_number: '',
  gcp_wif_pool_id: '',
  gcp_wif_provider_id: '',
  gcp_wif_target_sa_email: '',
  aws_access_key_id: '',
  aws_secret_access_key: '',
  aws_session_token: '',
  aws_region: '',
  aws_role_arn: '',
  aws_role_session_name: '',
  azure_client_id: '',
  azure_client_secret: '',
  azure_tenant_id: '',
  azure_subscription_id: '',
  azure_location: '',
  cloudflare_api_token: '',
})

export function gcpWifComplete(creds: CloudCredentialsForm | Record<string, string | null | undefined>): boolean {
  return Boolean(
    (creds.gcp_wif_project_number ?? '').toString().trim()
    && (creds.gcp_wif_pool_id ?? '').toString().trim()
    && (creds.gcp_wif_provider_id ?? '').toString().trim()
    && (creds.gcp_wif_target_sa_email ?? '').toString().trim(),
  )
}

export function hasGcpAuth(creds: CloudCredentialsForm | Record<string, string | null | undefined>): boolean {
  return Boolean((creds.gcp_sa_key_json ?? '').toString().trim()) || gcpWifComplete(creds)
}

export function hasAwsAuth(creds: CloudCredentialsForm | Record<string, string | null | undefined>): boolean {
  if ((creds.aws_role_arn ?? '').toString().trim()) return true
  return Boolean(
    (creds.aws_access_key_id ?? '').toString().trim()
    && (creds.aws_secret_access_key ?? '').toString().trim(),
  )
}

export const frameworkOptionSchema = z.enum([
  'react_vite',
  'nextjs',
  'nuxtjs',
  'vuejs',
  'svelte',
  'fastapi',
  'flask',
  'django',
  'express',
  'nestjs',
  'springboot',
  'go',
  'rust',
  'node',
  'python',
  'java',
  'generic',
])

export const dependencyPlacementSchema = z.enum(['in_cluster', 'managed', 'external'])

export const dataStoreDependencySchema = z.object({
  enabled: z.boolean().default(false),
  placement: dependencyPlacementSchema.default('in_cluster'),
  connection_url: z.string().max(2048).nullable().optional(),
})

export const workloadDependenciesSchema = z.object({
  postgres: dataStoreDependencySchema.default({ enabled: false, placement: 'in_cluster' }),
  mysql: dataStoreDependencySchema.default({ enabled: false, placement: 'in_cluster' }),
  mongodb: dataStoreDependencySchema.default({ enabled: false, placement: 'in_cluster' }),
  redis: dataStoreDependencySchema.default({ enabled: false, placement: 'in_cluster' }),
})

export const defaultWorkloadDependencies = (): z.infer<typeof workloadDependenciesSchema> => ({
  postgres: { enabled: false, placement: 'in_cluster' },
  mysql: { enabled: false, placement: 'in_cluster' },
  mongodb: { enabled: false, placement: 'in_cluster' },
  redis: { enabled: false, placement: 'in_cluster' },
})

export const containerServiceSpecSchema = z.object({
  name: z.string().trim().min(1).max(64).default('app'),
  stack: frameworkOptionSchema.default('node'),
  app_kind: z.enum(['frontend', 'backend']).default('backend'),
  listen_port: z.number().int().min(1).max(65535).default(8080),
  dockerfile_path: z.string().nullable().optional(),
  /** Open-app / browser target. Defaults: frontend true, backend false. */
  expose_preview: z.boolean().nullable().optional(),
})

export const containerScaffoldSchema = z.object({
  enabled: z.boolean().default(false),
  generate_dockerfile: z.boolean().default(true),
  generate_docker_compose: z.boolean().default(true),
  stack: frameworkOptionSchema.default('node'),
  frameworks: z.array(frameworkOptionSchema).default([]),
  app_name: z.string().trim().min(1).max(100).default('app'),
  listen_port: z.number().int().min(1).max(65535).default(8080),
  services: z.array(containerServiceSpecSchema).default([]),
})

/** Default frontend + backend pair shown in ContainerScaffoldCard. */
export const defaultContainerServices = (): z.infer<typeof containerServiceSpecSchema>[] => [
  {
    name: 'web-ui',
    app_kind: 'frontend',
    stack: 'nextjs',
    listen_port: 3000,
    expose_preview: true,
  },
  {
    name: 'api-server',
    app_kind: 'backend',
    stack: 'node',
    listen_port: 8080,
    expose_preview: false,
  },
]

export const defaultContainerScaffold = (): z.infer<typeof containerScaffoldSchema> => ({
  enabled: false,
  generate_dockerfile: true,
  generate_docker_compose: true,
  stack: 'nextjs',
  frameworks: [],
  app_name: 'web-ui',
  listen_port: 3000,
  services: [],
})

const wizardNameSchema = z
  .string()
  .trim()
  .toLowerCase()
  .min(3)
  .max(64)
  .regex(/^[a-z][a-z0-9-]*$/)

const wizardCommonFields = {
  name: wizardNameSchema,
  launchpad_project_id: z.string().uuid().optional().nullable(),
  iac_engine: iacEngineSchema.default('terraform'),
  credentials: cloudCredentialsSchema.default({}),
  run_init: z.boolean().default(true),
  runtime_mode: workspaceRuntimeModeSchema.default('kubernetes'),
  running_instance: runningInstanceSchema.default({
    kind: 'local_machine',
    service_name: null,
    region: null,
    host: null,
    ssh_user: 'ubuntu',
    ssh_port: 22,
    ssh_key_path: null,
    listen_port: 8080,
    process_strategy: 'docker',
    code_source: 'ssh',
    reverse_proxy: 'none',
    preview_url_override: null,
    kube_context: null,
    endpoint_url: null,
  }),
  kubernetes_options: kubernetesWorkloadOptionsSchema.default(defaultKubernetesWorkloadOptions()),
  cost_optimization: costOptimizationSchema.default(defaultCostOptimization()),
  container_scaffold: containerScaffoldSchema.default(defaultContainerScaffold()),
  dependencies: workloadDependenciesSchema.default(defaultWorkloadDependencies()),
  ansible: ansibleConfigSchema.default(defaultAnsibleConfig()),
}

export const provisioningWizardSchema = z.discriminatedUnion('provider', [
  z.object({
    ...wizardCommonFields,
    provider: z.literal('local'),
    resources: localResourcesSchema.default({
      cluster_name: 'launchpad',
      context: 'kind-launchpad',
    }),
    artifact_mode: workspaceArtifactsModeSchema.default('manifest_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('raw_manifests'),
  }),
  z.object({
    ...wizardCommonFields,
    provider: z.literal('gcp'),
    resources: gcpResourcesSchema,
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
  }),
  z.object({
    ...wizardCommonFields,
    provider: z.literal('aws'),
    resources: awsResourcesSchema,
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
  }),
  z.object({
    ...wizardCommonFields,
    provider: z.literal('azure'),
    resources: azureResourcesSchema,
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
  }),
  z.object({
    ...wizardCommonFields,
    provider: z.literal('cloudflare'),
    resources: cloudflareResourcesSchema,
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
  }),
]).superRefine((value, ctx) => {
  if (value.runtime_mode === 'docker_compose' && value.provider !== 'local') {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Docker Compose runtime is local-only',
      path: ['runtime_mode'],
    })
    return
  }

  if (value.runtime_mode === 'docker_compose') {
    if (value.kubernetes_packaging !== 'none') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Compose runtime does not use Kubernetes packaging',
        path: ['kubernetes_packaging'],
      })
    }
    return
  }

  if (value.runtime_mode === 'running_instance') {
    const serverless =
      (value.provider === 'gcp' && value.resources.cloud_run) ||
      (value.provider === 'aws' && value.resources.app_runner) ||
      (value.provider === 'azure' && value.resources.container_apps)
    if (value.running_instance.kind === 'serverless' && !serverless) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Enable Cloud Run, App Runner, or Container Apps for serverless compute',
        path: ['running_instance', 'kind'],
      })
    }
    if (value.running_instance.kind === 'local_machine' && value.provider !== 'local') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'local_machine is only available for the local provider',
        path: ['running_instance', 'kind'],
      })
    }
    if (value.running_instance.kind === 'vm') {
      const hasHost = Boolean(value.running_instance.host?.trim())
      const hasOverride = Boolean(
        value.running_instance.preview_url_override?.trim()
        || value.running_instance.endpoint_url?.trim(),
      )
      // local falls back to a Docker preview; GCP/AWS auto-create the VM. Only
      // Azure (no auto-provision yet) still requires a host up front.
      const canAutocreate = ['local', 'gcp', 'aws'].includes(value.provider)
      if (!hasHost && !hasOverride && !canAutocreate) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'VM host (IP/hostname) is required for Azure',
          path: ['running_instance', 'host'],
        })
      }
    }
    return
  }

  // kubernetes (and other modes that did not early-return)
  if (value.provider === 'local') {
    if (value.runtime_mode === 'kubernetes' && value.artifact_mode !== 'manifest_only') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Local Kubernetes workspaces use manifest-only artifacts',
        path: ['artifact_mode'],
      })
    }
    return
  }
  if (value.artifact_mode === 'iac_only') return
  if (value.kubernetes_packaging === 'none') {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Choose Raw manifests or Helm when artifact mode includes manifests',
      path: ['kubernetes_packaging'],
    })
    return
  }
  const hasRuntime =
    (value.provider === 'gcp' && (value.resources.gke || value.resources.cloud_run)) ||
    (value.provider === 'aws' && value.resources.eks) ||
    (value.provider === 'azure' &&
      (value.resources.aks || value.resources.container_apps))
  if (!hasRuntime) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message:
        'Select GKE/EKS/AKS (or Cloud Run / Container Apps) before choosing Kubernetes packaging',
      path: ['kubernetes_packaging'],
    })
  }
})

export const githubRepoSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Repository name is required')
    .max(100)
    .regex(/^[A-Za-z0-9_.-]+$/, 'Use letters, numbers, dots, underscores, or hyphens'),
  description: z.string().max(350).default(''),
  private: z.boolean().default(true),
  installation_id: z.preprocess((value) => {
    if (value === '' || value === undefined) return null
    if (typeof value === 'string' && value.trim()) return Number(value)
    return value
  }, z.number({ invalid_type_error: 'Select a GitHub account' }).int().positive().nullable().optional()),
  organization: z.preprocess(
    (value) => (value === '' || value === undefined ? null : value),
    z.string().max(100).nullable().optional(),
  ),
  workspace_id: z.preprocess(
    (value) => (value === '' || value === undefined ? null : value),
    z.string().uuid('Workspace id must be a valid UUID').nullable().optional(),
  ),
  set_cloud_secrets: z.boolean().default(true),
  include_workflow: z.boolean().default(true),
  include_dockerfiles: z.boolean().default(true),
  existing_full_name: z.preprocess(
    (value) => (value === '' || value === undefined ? null : value),
    z
      .string()
      .max(200)
      .regex(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/, 'Use owner/repo form')
      .nullable()
      .optional(),
  ),
})

export type ProvisioningWizardInput = z.infer<typeof provisioningWizardSchema>
export type GitHubRepoInput = z.infer<typeof githubRepoSchema>
