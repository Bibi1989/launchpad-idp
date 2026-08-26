import type { CloudProvider } from '~/types/provisioning'

export type CloudServiceOptionChoice = {
  value: string
  label: string
}

export type CloudServiceNestedOption = {
  /** Field on the provider resources object (e.g. cloud_sql_engine). */
  field: string
  label: string
  choices: CloudServiceOptionChoice[]
}

export type CloudServiceOption = {
  key: string
  title: string
  desc?: string
  /** Extra selects shown when this service checkbox is enabled. */
  nestedOptions?: CloudServiceNestedOption[]
}

const SQL_ENGINE_CHOICES_GCP: CloudServiceOptionChoice[] = [
  { value: 'postgres', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
]

const SQL_ENGINE_CHOICES_AWS: CloudServiceOptionChoice[] = [
  { value: 'postgres', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'mariadb', label: 'MariaDB' },
]

const CACHE_ENGINE_CHOICES: CloudServiceOptionChoice[] = [
  { value: 'redis', label: 'Redis' },
  { value: 'memcached', label: 'Memcached' },
]

const COSMOS_API_CHOICES: CloudServiceOptionChoice[] = [
  { value: 'mongodb', label: 'MongoDB API' },
  { value: 'sql', label: 'NoSQL (Core / SQL) API' },
]

const LAMBDA_RUNTIME_CHOICES: CloudServiceOptionChoice[] = [
  { value: 'nodejs20.x', label: 'Node.js 20' },
  { value: 'python3.12', label: 'Python 3.12' },
  { value: 'provided.al2023', label: 'Custom (AL2023)' },
]

export const GCP_SERVICE_OPTIONS: CloudServiceOption[] = [
  { key: 'vpc', title: 'VPC', desc: 'Isolated network with custom routing' },
  { key: 'subnets', title: 'Subnets', desc: 'Regional subnet layout' },
  { key: 'gke', title: 'GKE', desc: 'Managed Kubernetes cluster' },
  { key: 'artifact_registry', title: 'Artifact Registry', desc: 'Container image storage' },
  { key: 'cloud_run', title: 'Cloud Run', desc: 'Serverless containers' },
  { key: 'compute_instance', title: 'Compute Engine', desc: 'GCE VM for SSH / Docker workloads' },
  { key: 'cloud_functions', title: 'Cloud Functions', desc: 'Event-driven functions' },
  {
    key: 'cloud_sql',
    title: 'Cloud SQL',
    desc: 'Managed relational database',
    nestedOptions: [
      {
        field: 'cloud_sql_engine',
        label: 'Database engine',
        choices: SQL_ENGINE_CHOICES_GCP,
      },
    ],
  },
  { key: 'cloud_storage', title: 'Cloud Storage', desc: 'Object storage buckets' },
  { key: 'pubsub', title: 'Pub/Sub', desc: 'Messaging topics & subscriptions' },
  {
    key: 'memorystore',
    title: 'Memorystore',
    desc: 'Managed cache',
    nestedOptions: [
      {
        field: 'memorystore_engine',
        label: 'Cache engine',
        choices: CACHE_ENGINE_CHOICES,
      },
    ],
  },
  { key: 'bigquery', title: 'BigQuery', desc: 'Analytics warehouse dataset' },
]

export const AWS_SERVICE_OPTIONS: CloudServiceOption[] = [
  { key: 'vpc', title: 'VPC' },
  { key: 'subnets', title: 'Subnets' },
  { key: 'ec2', title: 'EC2' },
  { key: 's3', title: 'S3' },
  { key: 'eks', title: 'EKS' },
  { key: 'secrets_manager', title: 'Secrets Manager' },
  {
    key: 'rds',
    title: 'RDS',
    nestedOptions: [
      {
        field: 'rds_engine',
        label: 'Database engine',
        choices: SQL_ENGINE_CHOICES_AWS,
      },
    ],
  },
  { key: 'ecr', title: 'ECR' },
  {
    key: 'app_runner',
    title: 'App Runner',
    desc: 'Managed containers from a Docker image (similar to Cloud Run)',
  },
  {
    key: 'elasticache',
    title: 'ElastiCache',
    nestedOptions: [
      {
        field: 'elasticache_engine',
        label: 'Cache engine',
        choices: CACHE_ENGINE_CHOICES,
      },
    ],
  },
  {
    key: 'lambda_fn',
    title: 'Lambda',
    nestedOptions: [
      {
        field: 'lambda_runtime',
        label: 'Runtime',
        choices: LAMBDA_RUNTIME_CHOICES,
      },
    ],
  },
  { key: 'dynamodb', title: 'DynamoDB' },
  { key: 'sqs', title: 'SQS' },
  { key: 'alb', title: 'ALB' },
]

export const AZURE_SERVICE_OPTIONS: CloudServiceOption[] = [
  { key: 'vnet', title: 'VNet' },
  { key: 'subnets', title: 'Subnets' },
  { key: 'aks', title: 'AKS' },
  { key: 'key_vault', title: 'Key Vault' },
  { key: 'container_apps', title: 'Container Apps' },
  { key: 'acr', title: 'ACR' },
  { key: 'storage_account', title: 'Storage' },
  {
    key: 'cosmos_db',
    title: 'Cosmos DB',
    nestedOptions: [
      {
        field: 'cosmos_api',
        label: 'API',
        choices: COSMOS_API_CHOICES,
      },
    ],
  },
  { key: 'redis_cache', title: 'Redis Cache' },
  { key: 'app_service', title: 'App Service' },
  { key: 'log_analytics', title: 'Log Analytics' },
]

export const CLOUDFLARE_SERVICE_OPTIONS: CloudServiceOption[] = [
  { key: 'workers', title: 'Workers' },
  { key: 'r2', title: 'R2' },
  { key: 'dns_records', title: 'DNS' },
  { key: 'pages', title: 'Pages' },
  { key: 'kv', title: 'KV' },
  { key: 'd1', title: 'D1' },
  { key: 'tunnels', title: 'Tunnels' },
  { key: 'queues', title: 'Queues' },
]

export function cloudServiceOptionsForProvider(provider: CloudProvider): CloudServiceOption[] {
  if (provider === 'gcp') return GCP_SERVICE_OPTIONS
  if (provider === 'aws') return AWS_SERVICE_OPTIONS
  if (provider === 'azure') return AZURE_SERVICE_OPTIONS
  if (provider === 'cloudflare') return CLOUDFLARE_SERVICE_OPTIONS
  return []
}

/** Enabled boolean resource keys from a cloud resources object. */
export function enabledCloudServices(
  provider: CloudProvider,
  resources: Record<string, unknown>,
): CloudServiceOption[] {
  return cloudServiceOptionsForProvider(provider).filter((opt) => resources[opt.key] === true)
}

/** True when a managed Kubernetes cluster service is selected (not Cloud Run / Container Apps). */
export function hasKubernetesClusterService(
  provider: CloudProvider,
  resources: Record<string, unknown>,
): boolean {
  if (provider === 'local') return true
  if (provider === 'gcp') return resources.gke === true
  if (provider === 'aws') return resources.eks === true
  if (provider === 'azure') return resources.aks === true
  return false
}
