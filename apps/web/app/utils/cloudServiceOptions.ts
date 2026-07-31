import type { CloudProvider } from '~/types/provisioning'

export type CloudServiceOption = {
  key: string
  title: string
  desc?: string
}

export const GCP_SERVICE_OPTIONS: CloudServiceOption[] = [
  { key: 'vpc', title: 'VPC', desc: 'Isolated network with custom routing' },
  { key: 'subnets', title: 'Subnets', desc: 'Regional subnet layout' },
  { key: 'gke', title: 'GKE', desc: 'Managed Kubernetes cluster' },
  { key: 'artifact_registry', title: 'Artifact Registry', desc: 'Container image storage' },
  { key: 'cloud_run', title: 'Cloud Run', desc: 'Serverless containers' },
  { key: 'cloud_functions', title: 'Cloud Functions', desc: 'Event-driven functions' },
  { key: 'cloud_sql', title: 'Cloud SQL', desc: 'Managed PostgreSQL / MySQL' },
  { key: 'cloud_storage', title: 'Cloud Storage', desc: 'Object storage buckets' },
  { key: 'pubsub', title: 'Pub/Sub', desc: 'Messaging topics & subscriptions' },
  { key: 'memorystore', title: 'Memorystore', desc: 'Managed Redis' },
  { key: 'bigquery', title: 'BigQuery', desc: 'Analytics warehouse dataset' },
]

export const AWS_SERVICE_OPTIONS: CloudServiceOption[] = [
  { key: 'vpc', title: 'VPC' },
  { key: 'subnets', title: 'Subnets' },
  { key: 'ec2', title: 'EC2' },
  { key: 's3', title: 'S3' },
  { key: 'eks', title: 'EKS' },
  { key: 'secrets_manager', title: 'Secrets Manager' },
  { key: 'rds', title: 'RDS' },
  { key: 'ecr', title: 'ECR' },
  { key: 'elasticache', title: 'ElastiCache' },
  { key: 'lambda_fn', title: 'Lambda' },
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
  { key: 'cosmos_db', title: 'Cosmos DB' },
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
