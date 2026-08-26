import {
  credential,
  region,
  tier,
  type CloudProviderCatalogEntry,
} from '../cloud-providers.types';

/**
 * The provider catalog, ported 1:1 from the FastAPI adapters. Order matches the
 * Python registry so the UI grid is identical across backends.
 */
export const PROVIDER_CATALOG: CloudProviderCatalogEntry[] = [
  {
    id: 'hetzner',
    label: 'Hetzner Cloud',
    docs_url: 'https://docs.hetzner.cloud/',
    runtime_targets: ['vm', 'docker_host'],
    credential_fields: [
      credential('api_token', 'API Token', {
        help: 'Project API token from Hetzner Cloud Console (Security > API Tokens).',
        placeholder: 'hetzner-cloud-api-token',
      }),
    ],
    regions: [
      region('nbg1', 'Nuremberg (nbg1)'),
      region('fsn1', 'Falkenstein (fsn1)'),
      region('hel1', 'Helsinki (hel1)'),
      region('ash', 'Ashburn, VA (ash)'),
      region('hil', 'Hillsboro, OR (hil)'),
    ],
    tiers: [
      tier('cx22', 'CX22 - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096, monthly_usd: 4.5 }),
      tier('cx32', 'CX32 - 4 vCPU / 8 GB', { vcpus: 4, memory_mb: 8192, monthly_usd: 8.0 }),
      tier('cpx11', 'CPX11 - 2 vCPU / 2 GB', { vcpus: 2, memory_mb: 2048, monthly_usd: 4.0 }),
      tier('cpx21', 'CPX21 - 3 vCPU / 4 GB', { vcpus: 3, memory_mb: 4096, monthly_usd: 7.0 }),
    ],
  },
  {
    id: 'digitalocean',
    label: 'DigitalOcean',
    docs_url: 'https://docs.digitalocean.com/reference/api/',
    runtime_targets: ['vm', 'docker_host'],
    credential_fields: [
      credential('api_token', 'API Token', {
        help: 'Personal access token with read+write scope (API > Tokens).',
        placeholder: 'dop_v1_...',
      }),
    ],
    regions: [
      region('nyc1', 'New York 1 (nyc1)'),
      region('nyc3', 'New York 3 (nyc3)'),
      region('sfo3', 'San Francisco 3 (sfo3)'),
      region('ams3', 'Amsterdam 3 (ams3)'),
      region('fra1', 'Frankfurt 1 (fra1)'),
      region('lon1', 'London 1 (lon1)'),
      region('sgp1', 'Singapore 1 (sgp1)'),
    ],
    tiers: [
      tier('s-1vcpu-1gb', 'Basic - 1 vCPU / 1 GB', { vcpus: 1, memory_mb: 1024, monthly_usd: 6.0 }),
      tier('s-1vcpu-2gb', 'Basic - 1 vCPU / 2 GB', { vcpus: 1, memory_mb: 2048, monthly_usd: 12.0 }),
      tier('s-2vcpu-2gb', 'Basic - 2 vCPU / 2 GB', { vcpus: 2, memory_mb: 2048, monthly_usd: 18.0 }),
      tier('s-2vcpu-4gb', 'Basic - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096, monthly_usd: 24.0 }),
    ],
  },
  {
    id: 'linode',
    label: 'Akamai Linode',
    docs_url: 'https://techdocs.akamai.com/linode-api/reference/api-summary',
    runtime_targets: ['vm', 'docker_host'],
    credential_fields: [
      credential('api_token', 'Personal Access Token', {
        help: 'Linode PAT with Linodes read/write scope (Cloud Manager > API Tokens).',
        placeholder: 'linode-personal-access-token',
      }),
    ],
    regions: [
      region('us-east', 'Newark, NJ (us-east)'),
      region('us-central', 'Dallas, TX (us-central)'),
      region('us-ord', 'Chicago, IL (us-ord)'),
      region('us-west', 'Fremont, CA (us-west)'),
      region('fr-par', 'Paris (fr-par)'),
      region('eu-west', 'London (eu-west)'),
      region('eu-central', 'Frankfurt (eu-central)'),
      region('ap-south', 'Singapore (ap-south)'),
      region('ap-northeast', 'Tokyo (ap-northeast)'),
    ],
    tiers: [
      tier('g6-nanode-1', 'Nanode 1GB - 1 vCPU / 1 GB', { vcpus: 1, memory_mb: 1024, monthly_usd: 5.0 }),
      tier('g6-standard-1', 'Linode 2GB - 1 vCPU / 2 GB', { vcpus: 1, memory_mb: 2048, monthly_usd: 10.0 }),
      tier('g6-standard-2', 'Linode 4GB - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096, monthly_usd: 20.0 }),
      tier('g6-standard-4', 'Linode 8GB - 4 vCPU / 8 GB', { vcpus: 4, memory_mb: 8192, monthly_usd: 40.0 }),
      tier('g6-standard-6', 'Linode 16GB - 6 vCPU / 16 GB', { vcpus: 6, memory_mb: 16384, monthly_usd: 80.0 }),
      tier('g6-dedicated-2', 'Dedicated 4GB - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096, monthly_usd: 30.0 }),
    ],
  },
  {
    id: 'aws',
    label: 'Amazon Web Services (EC2)',
    docs_url: 'https://docs.aws.amazon.com/ec2/',
    runtime_targets: ['vm', 'docker_host'],
    credential_fields: [
      credential('aws_access_key_id', 'Access Key ID'),
      credential('aws_secret_access_key', 'Secret Access Key'),
      credential('aws_session_token', 'Session Token', {
        required: false,
        help: 'Only for temporary STS credentials.',
      }),
      credential('aws_region', 'Region', { secret: false, required: false, placeholder: 'us-east-1' }),
    ],
    regions: [
      region('us-east-1', 'US East (N. Virginia)'),
      region('us-east-2', 'US East (Ohio)'),
      region('us-west-2', 'US West (Oregon)'),
      region('eu-west-1', 'EU (Ireland)'),
      region('eu-central-1', 'EU (Frankfurt)'),
      region('ap-southeast-1', 'Asia Pacific (Singapore)'),
    ],
    tiers: [
      tier('t3.micro', 't3.micro - 2 vCPU / 1 GB', { vcpus: 2, memory_mb: 1024 }),
      tier('t3.small', 't3.small - 2 vCPU / 2 GB', { vcpus: 2, memory_mb: 2048 }),
      tier('t3.medium', 't3.medium - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096 }),
      tier('t3.large', 't3.large - 2 vCPU / 8 GB', { vcpus: 2, memory_mb: 8192 }),
    ],
  },
  {
    id: 'gcp',
    label: 'Google Cloud (Compute Engine)',
    docs_url: 'https://cloud.google.com/compute/docs/reference/rest/v1',
    runtime_targets: ['vm', 'docker_host'],
    credential_fields: [
      credential('gcp_sa_key_json', 'Service Account JSON', {
        help: 'Key JSON for a service account with Compute Admin.',
      }),
      credential('gcp_project_id', 'Project ID', {
        secret: false,
        required: false,
        help: 'Defaults to the project inside the key JSON.',
      }),
      credential('gcp_region', 'Region', { secret: false, required: false, placeholder: 'us-central1' }),
    ],
    regions: [
      region('us-central1', 'Iowa (us-central1)'),
      region('us-east1', 'South Carolina (us-east1)'),
      region('us-west1', 'Oregon (us-west1)'),
      region('europe-west1', 'Belgium (europe-west1)'),
      region('europe-west3', 'Frankfurt (europe-west3)'),
      region('europe-west4', 'Netherlands (europe-west4)'),
      region('asia-southeast1', 'Singapore (asia-southeast1)'),
    ],
    tiers: [
      tier('e2-small', 'e2-small - 2 vCPU / 2 GB', { vcpus: 2, memory_mb: 2048 }),
      tier('e2-medium', 'e2-medium - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096 }),
      tier('e2-standard-2', 'e2-standard-2 - 2 vCPU / 8 GB', { vcpus: 2, memory_mb: 8192 }),
      tier('e2-standard-4', 'e2-standard-4 - 4 vCPU / 16 GB', { vcpus: 4, memory_mb: 16384 }),
    ],
  },
  {
    id: 'azure',
    label: 'Microsoft Azure (VM)',
    docs_url: 'https://learn.microsoft.com/rest/api/compute/',
    runtime_targets: ['vm', 'docker_host'],
    credential_fields: [
      credential('azure_client_id', 'Client ID'),
      credential('azure_client_secret', 'Client Secret'),
      credential('azure_tenant_id', 'Tenant ID'),
      credential('azure_subscription_id', 'Subscription ID'),
      credential('azure_location', 'Location', { secret: false, required: false, placeholder: 'eastus' }),
    ],
    regions: [
      region('eastus', 'East US'),
      region('eastus2', 'East US 2'),
      region('westus2', 'West US 2'),
      region('westeurope', 'West Europe'),
      region('northeurope', 'North Europe'),
      region('southeastasia', 'Southeast Asia'),
    ],
    tiers: [
      tier('Standard_B1s', 'B1s - 1 vCPU / 1 GB', { vcpus: 1, memory_mb: 1024 }),
      tier('Standard_B2s', 'B2s - 2 vCPU / 4 GB', { vcpus: 2, memory_mb: 4096 }),
      tier('Standard_B2ms', 'B2ms - 2 vCPU / 8 GB', { vcpus: 2, memory_mb: 8192 }),
      tier('Standard_D2s_v5', 'D2s v5 - 2 vCPU / 8 GB', { vcpus: 2, memory_mb: 8192 }),
    ],
  },
  {
    id: 'railway',
    label: 'Railway',
    docs_url: 'https://docs.railway.com/reference/public-api',
    runtime_targets: ['paas'],
    credential_fields: [
      credential('api_token', 'Account or Team Token', {
        help: 'Railway API token (Account Settings > Tokens).',
        placeholder: 'railway-api-token',
      }),
    ],
    regions: [
      region('us-west1', 'US West (us-west1)'),
      region('us-east4', 'US East (us-east4)'),
      region('europe-west4', 'EU West (europe-west4)'),
      region('asia-southeast1', 'Asia SE (asia-southeast1)'),
    ],
    tiers: [],
  },
  {
    id: 'render',
    label: 'Render',
    docs_url: 'https://render.com/docs/api',
    runtime_targets: ['paas'],
    credential_fields: [
      credential('api_key', 'API Key', {
        help: 'Render API key (Account Settings > API Keys).',
        placeholder: 'rnd_...',
      }),
      credential('owner_id', 'Owner ID', {
        secret: false,
        required: false,
        help: 'Workspace/owner id (usr-... or tea-...); the first owner is used if omitted.',
      }),
    ],
    regions: [
      region('oregon', 'Oregon, USA (oregon)'),
      region('ohio', 'Ohio, USA (ohio)'),
      region('virginia', 'Virginia, USA (virginia)'),
      region('frankfurt', 'Frankfurt, Germany (frankfurt)'),
      region('singapore', 'Singapore (singapore)'),
    ],
    tiers: [
      tier('starter', 'Starter - 0.5 CPU / 512 MB', { memory_mb: 512, monthly_usd: 7.0 }),
      tier('standard', 'Standard - 1 CPU / 2 GB', { vcpus: 1, memory_mb: 2048, monthly_usd: 25.0 }),
      tier('pro', 'Pro - 2 CPU / 4 GB', { vcpus: 2, memory_mb: 4096, monthly_usd: 85.0 }),
      tier('pro_plus', 'Pro Plus - 4 CPU / 8 GB', { vcpus: 4, memory_mb: 8192, monthly_usd: 175.0 }),
      tier('pro_max', 'Pro Max - 4 CPU / 16 GB', { vcpus: 4, memory_mb: 16384, monthly_usd: 225.0 }),
      tier('pro_ultra', 'Pro Ultra - 8 CPU / 32 GB', { vcpus: 8, memory_mb: 32768, monthly_usd: 450.0 }),
    ],
  },
  {
    id: 'cloudflare',
    label: 'Cloudflare Workers',
    docs_url: 'https://developers.cloudflare.com/workers/',
    runtime_targets: ['paas'],
    credential_fields: [
      credential('cloudflare_api_token', 'API Token', {
        help: 'Token with Workers Scripts:Edit permission.',
      }),
      credential('cloudflare_account_id', 'Account ID', {
        secret: false,
        required: false,
        help: 'Optional; the first accessible account is used when omitted.',
      }),
    ],
    regions: [],
    tiers: [],
  },
  // Legacy bridges (delegate to the existing CLI engine in FastAPI). Kept for parity.
  {
    id: 'gcp-legacy',
    label: 'Google Cloud (VM, legacy engine)',
    docs_url: null,
    runtime_targets: ['vm'],
    credential_fields: [
      credential('gcp_sa_key_json', 'Service Account JSON', {
        help: 'GCP service account key JSON with Compute Admin.',
      }),
      credential('gcp_project_id', 'GCP Project ID', { secret: false }),
      credential('gcp_region', 'Region', { secret: false, required: false, placeholder: 'us-central1' }),
    ],
    regions: [region('us-central1', 'us-central1'), region('europe-west1', 'europe-west1')],
    tiers: [],
  },
  {
    id: 'aws-legacy',
    label: 'Amazon Web Services (VM, legacy engine)',
    docs_url: null,
    runtime_targets: ['vm'],
    credential_fields: [
      credential('aws_access_key_id', 'Access Key ID'),
      credential('aws_secret_access_key', 'Secret Access Key'),
      credential('aws_region', 'Region', { secret: false, required: false, placeholder: 'us-east-1' }),
    ],
    regions: [region('us-east-1', 'us-east-1'), region('eu-west-1', 'eu-west-1')],
    tiers: [],
  },
  {
    id: 'azure-legacy',
    label: 'Microsoft Azure (VM, legacy engine)',
    docs_url: null,
    runtime_targets: ['vm'],
    credential_fields: [
      credential('azure_client_id', 'Client ID'),
      credential('azure_client_secret', 'Client Secret'),
      credential('azure_tenant_id', 'Tenant ID'),
      credential('azure_subscription_id', 'Subscription ID'),
      credential('azure_location', 'Location', { secret: false, required: false, placeholder: 'eastus' }),
    ],
    regions: [region('eastus', 'eastus'), region('westeurope', 'westeurope')],
    tiers: [],
  },
];

export function findProvider(id: string): CloudProviderCatalogEntry | undefined {
  return PROVIDER_CATALOG.find((provider) => provider.id === id);
}
