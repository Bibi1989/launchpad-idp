import { z } from 'zod'

export const cloudProviderSchema = z.enum(['local', 'gcp', 'aws', 'azure', 'cloudflare'])
export const iacEngineSchema = z.enum(['terraform', 'opentofu', 'pulumi'])
export const secretBackendSchema = z.enum(['secret_manager', 'native_k8s'])
export const kubernetesPackagingSchema = z.enum(['none', 'raw_manifests', 'helm'])
export const workspaceArtifactsModeSchema = z.enum(['iac_only', 'manifest_only', 'both'])
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
})

export const gcpResourcesSchema = z.object({
  vpc: z.boolean().default(true),
  subnets: z.boolean().default(true),
  gke: z.boolean().default(false),
  artifact_registry: z.boolean().default(false),
  secret_backend: secretBackendSchema.default('secret_manager'),
  cloud_run: z.boolean().default(false),
  cloud_functions: z.boolean().default(false),
  cloud_sql: z.boolean().default(false),
  cloud_storage: z.boolean().default(false),
  pubsub: z.boolean().default(false),
  memorystore: z.boolean().default(false),
  bigquery: z.boolean().default(false),
  region: z.string().min(2).max(64).default('us-central1'),
  project_id: z
    .string()
    .trim()
    .min(3, 'GCP Project ID is required (at least 3 characters)')
    .max(64)
    .regex(/^[a-z][a-z0-9-]*$/, 'Use lowercase letters, numbers, and hyphens'),
})

export const awsResourcesSchema = z.object({
  vpc: z.boolean().default(true),
  subnets: z.boolean().default(true),
  ec2: z.boolean().default(false),
  s3: z.boolean().default(false),
  eks: z.boolean().default(false),
  secrets_manager: z.boolean().default(true),
  rds: z.boolean().default(false),
  ecr: z.boolean().default(false),
  elasticache: z.boolean().default(false),
  lambda_fn: z.boolean().default(false),
  dynamodb: z.boolean().default(false),
  sqs: z.boolean().default(false),
  alb: z.boolean().default(false),
  region: z.string().min(2).max(32).default('us-east-1'),
  account_alias: z.string().max(64).optional().nullable(),
})

export const azureResourcesSchema = z.object({
  vnet: z.boolean().default(true),
  subnets: z.boolean().default(true),
  aks: z.boolean().default(false),
  key_vault: z.boolean().default(true),
  container_apps: z.boolean().default(false),
  acr: z.boolean().default(false),
  storage_account: z.boolean().default(false),
  cosmos_db: z.boolean().default(false),
  redis_cache: z.boolean().default(false),
  app_service: z.boolean().default(false),
  log_analytics: z.boolean().default(false),
  location: z.string().min(2).max(64).default('eastus'),
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
  aws_access_key_id: z.string().optional().nullable(),
  aws_secret_access_key: z.string().optional().nullable(),
  aws_session_token: z.string().optional().nullable(),
  azure_client_id: z.string().optional().nullable(),
  azure_client_secret: z.string().optional().nullable(),
  azure_tenant_id: z.string().optional().nullable(),
  azure_subscription_id: z.string().optional().nullable(),
  cloudflare_api_token: z.string().optional().nullable(),
})

export const provisioningWizardSchema = z.discriminatedUnion('provider', [
  z.object({
    name: z
      .string()
      .trim()
      .toLowerCase()
      .min(3)
      .max(64)
      .regex(/^[a-z][a-z0-9-]*$/),
    iac_engine: iacEngineSchema.default('terraform'),
    provider: z.literal('local'),
    resources: localResourcesSchema.default({
      cluster_name: 'launchpad',
      context: 'kind-launchpad',
    }),
    credentials: cloudCredentialsSchema.default({}),
    run_init: z.boolean().default(true),
    artifact_mode: workspaceArtifactsModeSchema.default('manifest_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('raw_manifests'),
    kubernetes_options: kubernetesWorkloadOptionsSchema.default(defaultKubernetesWorkloadOptions()),
    cost_optimization: costOptimizationSchema.default(defaultCostOptimization()),
  }),
  z.object({
    name: z
      .string()
      .trim()
      .toLowerCase()
      .min(3)
      .max(64)
      .regex(/^[a-z][a-z0-9-]*$/),
    iac_engine: iacEngineSchema.default('terraform'),
    provider: z.literal('gcp'),
    resources: gcpResourcesSchema,
    credentials: cloudCredentialsSchema.default({}),
    run_init: z.boolean().default(true),
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
    kubernetes_options: kubernetesWorkloadOptionsSchema.default(defaultKubernetesWorkloadOptions()),
    cost_optimization: costOptimizationSchema.default(defaultCostOptimization()),
  }),
  z.object({
    name: z
      .string()
      .trim()
      .toLowerCase()
      .min(3)
      .max(64)
      .regex(/^[a-z][a-z0-9-]*$/),
    iac_engine: iacEngineSchema.default('terraform'),
    provider: z.literal('aws'),
    resources: awsResourcesSchema,
    credentials: cloudCredentialsSchema.default({}),
    run_init: z.boolean().default(true),
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
    kubernetes_options: kubernetesWorkloadOptionsSchema.default(defaultKubernetesWorkloadOptions()),
    cost_optimization: costOptimizationSchema.default(defaultCostOptimization()),
  }),
  z.object({
    name: z
      .string()
      .trim()
      .toLowerCase()
      .min(3)
      .max(64)
      .regex(/^[a-z][a-z0-9-]*$/),
    iac_engine: iacEngineSchema.default('terraform'),
    provider: z.literal('azure'),
    resources: azureResourcesSchema,
    credentials: cloudCredentialsSchema.default({}),
    run_init: z.boolean().default(true),
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
    kubernetes_options: kubernetesWorkloadOptionsSchema.default(defaultKubernetesWorkloadOptions()),
    cost_optimization: costOptimizationSchema.default(defaultCostOptimization()),
  }),
  z.object({
    name: z
      .string()
      .trim()
      .toLowerCase()
      .min(3)
      .max(64)
      .regex(/^[a-z][a-z0-9-]*$/),
    iac_engine: iacEngineSchema.default('terraform'),
    provider: z.literal('cloudflare'),
    resources: cloudflareResourcesSchema,
    credentials: cloudCredentialsSchema.default({}),
    run_init: z.boolean().default(true),
    artifact_mode: workspaceArtifactsModeSchema.default('iac_only'),
    kubernetes_packaging: kubernetesPackagingSchema.default('none'),
    kubernetes_options: kubernetesWorkloadOptionsSchema.default(defaultKubernetesWorkloadOptions()),
    cost_optimization: costOptimizationSchema.default(defaultCostOptimization()),
  }),
]).superRefine((value, ctx) => {
  if (value.provider === 'local') {
    if (value.artifact_mode !== 'manifest_only') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Local (kind) supports manifest-only workspaces',
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
  if (value.provider === 'local') return
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
