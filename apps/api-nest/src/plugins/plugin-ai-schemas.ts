/**
 * Heuristic JSON Schema for plugin credentials + deploy config.
 * Mirrors apps/api/app/services/plugin_ai_schemas.py (Gemini lives on FastAPI).
 */

const DRAFT = 'http://json-schema.org/draft-07/schema#';
const TYPED = new Set(['gcp', 'aws', 'azure', 'cloudflare']);
const K8S = new Set(['gke', 'eks', 'aks', 'doks', 'lke', 'k3s']);
const CONTAINERS = new Set(['cloud-run', 'ecs-fargate', 'container-apps', 'aci', 'app-platform']);
const VMS = new Set([
  'gce',
  'gce-docker',
  'ec2',
  'ec2-docker',
  'azure-vm',
  'vm-docker',
  'droplet',
  'droplet-docker',
  'cloud-server',
  'server-docker',
  'linode-instance',
  'linode-docker',
]);
const PAAS = new Set(['workers', 'pages', 'railway-service', 'render-web', 'render-worker', 'tunnels', 'tunnel']);
const PARENTS = ['digitalocean', 'hetzner', 'linode', 'railway', 'render', 'cloudflare', 'gcp', 'aws', 'azure'];

type JsonSchema = Record<string, unknown>;

function prop(title: string, extra: Record<string, unknown> = {}): JsonSchema {
  return { type: extra.type ?? 'string', title, ...extra };
}

function objectSchema(
  properties: Record<string, JsonSchema>,
  opts: { required?: string[]; description?: string } = {},
): JsonSchema {
  return {
    $schema: DRAFT,
    type: 'object',
    additionalProperties: false,
    properties,
    ...(opts.required ? { required: opts.required } : {}),
    ...(opts.description ? { description: opts.description } : {}),
  };
}

function splitPluginId(pluginId: string): [string, string] {
  const pid = pluginId.trim().toLowerCase();
  for (const parent of [...PARENTS].sort((a, b) => b.length - a.length)) {
    if (pid === parent) return [parent, ''];
    if (pid.startsWith(`${parent}-`)) return [parent, pid.slice(parent.length + 1)];
  }
  return [pid, ''];
}

export function heuristicPluginSchemas(input: {
  parentCloud?: string;
  serviceType?: string;
  pluginId?: string;
  label?: string;
  category?: string;
  prompt?: string;
}): { credentialsSchema: JsonSchema; deploymentConfigSchema: JsonSchema } {
  const parentCloud = (input.parentCloud || '').trim().toLowerCase();
  const pluginId = (input.pluginId || '').trim().toLowerCase();
  let parent = parentCloud;
  let service = '';
  if (pluginId) {
    const [inferredParent, inferredService] = splitPluginId(pluginId);
    if (!parent) parent = inferredParent;
    if (inferredService) service = inferredService;
  }
  const blob = `${input.label || ''} ${input.prompt || ''} ${pluginId} ${input.serviceType || ''}`.toLowerCase();
  if (!parent) parent = guessParent(blob);
  if (!service) service = guessService(parent, input.serviceType || '', blob);
  return {
    credentialsSchema: credentialsSchema(parent, input.category || ''),
    deploymentConfigSchema: deploymentSchema(parent, service, input.serviceType || ''),
  };
}

function guessParent(text: string): string {
  const checks: Array<[string, string]> = [
    ['digitalocean', 'digitalocean'],
    ['droplet', 'digitalocean'],
    ['hetzner', 'hetzner'],
    ['gke', 'gcp'],
    ['gcp', 'gcp'],
    ['eks', 'aws'],
    ['aws', 'aws'],
    ['aks', 'azure'],
    ['azure', 'azure'],
    ['cloudflare', 'cloudflare'],
    ['workers', 'cloudflare'],
  ];
  for (const [token, parent] of checks) {
    if (text.includes(token)) return parent;
  }
  return '';
}

function guessService(parent: string, serviceType: string, text: string): string {
  for (const sid of ['gke', 'cloud-run', 'eks', 'aks', 'workers', 'droplet']) {
    if (text.includes(sid)) return sid;
  }
  if (text.includes('kubernetes') || serviceType === 'kubernetes') {
    return ({ gcp: 'gke', aws: 'eks', azure: 'aks', digitalocean: 'doks' } as Record<string, string>)[parent] || 'gke';
  }
  if (serviceType === 'container') {
    return ({ gcp: 'cloud-run', aws: 'ecs-fargate', azure: 'container-apps' } as Record<string, string>)[parent] || 'cloud-run';
  }
  if (serviceType === 'paas') return parent === 'cloudflare' ? 'workers' : 'railway-service';
  if (serviceType === 'vm') {
    return ({ gcp: 'gce', aws: 'ec2', azure: 'azure-vm', digitalocean: 'droplet' } as Record<string, string>)[parent] || 'gce';
  }
  return serviceType || parent;
}

function credentialsSchema(parent: string, category: string): JsonSchema {
  if (category === 'config') {
    return objectSchema(
      {
        ssh_user: prop('SSH user', { default: 'ubuntu' }),
        ssh_private_key: prop('SSH private key', { writeOnly: true }),
        inventory: prop('Inventory'),
      },
      { description: 'Optional connection overrides for a config plugin.' },
    );
  }
  if (TYPED.has(parent)) {
    const description = `Optional. Empty means use ${parent.toUpperCase()} keys from Settings. Values here override Settings for this plugin only.`;
    if (parent === 'gcp') {
      return objectSchema(
        { gcp_sa_key_json: prop('Service account JSON', { writeOnly: true }) },
        { description },
      );
    }
    if (parent === 'aws') {
      return objectSchema(
        {
          aws_access_key_id: prop('Access key ID'),
          aws_secret_access_key: prop('Secret access key', { writeOnly: true }),
        },
        { description },
      );
    }
    if (parent === 'azure') {
      return objectSchema(
        {
          azure_client_id: prop('Client ID'),
          azure_client_secret: prop('Client secret', { writeOnly: true }),
          azure_tenant_id: prop('Tenant ID'),
          azure_subscription_id: prop('Subscription ID'),
        },
        { description },
      );
    }
    return objectSchema(
      { cloudflare_api_token: prop('API token', { writeOnly: true }) },
      { description },
    );
  }
  const tokenName = parent === 'hetzner' || parent === 'railway' ? 'api_token' : parent === 'render' ? 'api_key' : 'token';
  const title = parent === 'render' ? 'API key' : 'API token';
  if (['digitalocean', 'hetzner', 'linode', 'railway', 'render'].includes(parent)) {
    return objectSchema(
      { [tokenName]: prop(title, { writeOnly: true }) },
      { required: [tokenName] },
    );
  }
  return objectSchema(
    { api_token: prop('API token', { writeOnly: true }) },
    { description: 'Plugin-specific credentials. Leave empty when a parent cloud is set.' },
  );
}

function deploymentSchema(parent: string, service: string, serviceType: string): JsonSchema {
  const sid = service.toLowerCase();
  if (K8S.has(sid) || serviceType === 'kubernetes') return k8sDeploy(parent, sid);
  if (CONTAINERS.has(sid)) return containerDeploy(parent, sid);
  if (PAAS.has(sid) || serviceType === 'paas') return paasDeploy(parent, sid);
  if (VMS.has(sid) || serviceType === 'vm') return vmDeploy(parent, sid);
  return serviceType === 'kubernetes' ? k8sDeploy(parent, sid) : vmDeploy(parent, sid);
}

function k8sDeploy(parent: string, service: string): JsonSchema {
  const regionDefault = ({ gcp: 'us-central1', aws: 'us-east-1', azure: 'eastus' } as Record<string, string>)[parent] || 'us-central1';
  const machine = parent === 'azure' ? 'vmSize' : 'machineType';
  const secretEnum = parent === 'aws' ? ['secrets_manager', 'native_k8s'] : parent === 'azure' ? ['key_vault', 'native_k8s'] : ['secret_manager', 'native_k8s'];
  const registryKey = parent === 'aws' ? 'ecr' : parent === 'azure' ? 'acr' : 'artifactRegistry';
  return objectSchema(
    {
      region: prop(parent === 'azure' ? 'Location' : 'Region', { default: regionDefault }),
      clusterName: prop('Cluster name'),
      nodeCount: prop('Node count', { type: 'integer', minimum: 1, default: 1 }),
      [machine]: prop('Node size'),
      imageSource: prop('Container image source', {
        enum: ['build_registry', 'external'],
        default: 'build_registry',
        description: 'build_registry provisions a native registry; external uses GHCR/Docker Hub/etc.',
      }),
      [registryKey]: prop('Provision image registry', { type: 'boolean', default: true }),
      secretBackend: prop('Secret backend', { enum: secretEnum, default: secretEnum[0] }),
      vpc: prop('Create VPC / network', { type: 'boolean', default: true }),
      subnets: prop('Create subnets', { type: 'boolean', default: true }),
    },
    {
      required: ['region'],
      description: `Deploy config for ${service || 'managed Kubernetes'} on ${parent || 'this cloud'}.`,
    },
  );
}

function containerDeploy(parent: string, service: string): JsonSchema {
  return objectSchema(
    {
      region: prop('Region', { default: parent === 'gcp' ? 'us-central1' : 'us-east-1' }),
      serviceName: prop('Service name'),
      image: prop('Container image'),
      imageSource: prop('Container image source', { enum: ['build_registry', 'external'], default: 'build_registry' }),
      cpu: prop('CPU', { default: '1' }),
      memory: prop('Memory', { default: '512Mi' }),
      port: prop('App port', { type: 'integer', default: 8080 }),
      allowUnauthenticated: prop('Allow unauthenticated', { type: 'boolean', default: true }),
    },
    { required: ['region'], description: `Deploy config for ${service || 'serverless containers'} on ${parent || 'this cloud'}.` },
  );
}

function vmDeploy(parent: string, service: string): JsonSchema {
  const sizeKey = ['digitalocean', 'hetzner', 'linode'].includes(parent) ? 'size' : 'machineType';
  return objectSchema(
    {
      region: prop('Region / location'),
      zone: prop('Zone'),
      [sizeKey]: prop('Machine size'),
      image: prop('OS image', { default: 'ubuntu-24.04' }),
      sshUser: prop('SSH user', { default: 'ubuntu' }),
      appPort: prop('App port', { type: 'integer', default: 8080 }),
      installDocker: prop('Install Docker via Launchpad script', { type: 'boolean', default: true }),
    },
    { required: ['region'], description: `Deploy config for ${service || 'VM'} on ${parent || 'this cloud'}.` },
  );
}

function paasDeploy(parent: string, service: string): JsonSchema {
  if (parent === 'cloudflare') {
    return objectSchema(
      {
        accountId: prop('Account ID'),
        name: prop('Worker / Pages project name'),
        compatibilityDate: prop('Compatibility date', { default: '2024-01-01' }),
      },
      { description: `Deploy config for Cloudflare ${service || 'Workers'}.` },
    );
  }
  return objectSchema(
    {
      region: prop('Region'),
      name: prop('Service name'),
      image: prop('Container image'),
    },
    { description: `Deploy config for ${service || 'PaaS'} on ${parent || 'this cloud'}.` },
  );
}
