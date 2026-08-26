/**
 * Heuristic plugin-manifest generator (Gemini lives on the FastAPI control plane).
 * Kept so Nest /plugins/generate stays shape-compatible.
 */

import { heuristicPluginSchemas } from './plugin-ai-schemas';

const SEMVER = '1.0.0';

function slugify(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug || 'cloud-plugin';
}

export function heuristicPluginManifest(prompt: string): Record<string, unknown> {
  const text = prompt.toLowerCase();
  let parent = '';
  let serviceType = 'vm';
  let icon = 'cloud';
  let runnerType = 'terraform';
  let label = prompt.trim().slice(0, 64);
  let slug = slugify(label);

  if (['gke', 'gcp', 'google cloud', 'cloud run', 'compute engine'].some((token) => text.includes(token))) {
    parent = 'gcp';
    icon = 'cloud_sync';
    if (text.includes('gke') || text.includes('kubernetes')) {
      serviceType = 'kubernetes';
      icon = 'hub';
      slug = 'gcp-gke';
      label = 'Google GKE';
    } else if (text.includes('cloud run')) {
      serviceType = 'container';
      icon = 'directions_run';
      slug = 'gcp-cloud-run';
      label = 'Cloud Run';
    } else {
      slug = 'gcp-compute';
      label = 'Compute Engine';
    }
  } else if (['eks', 'ecs', 'aws', 'amazon', 'lambda', 'ec2'].some((token) => text.includes(token))) {
    parent = 'aws';
    icon = 'cloud_upload';
    if (text.includes('eks') || text.includes('kubernetes')) {
      serviceType = 'kubernetes';
      icon = 'hub';
      slug = 'aws-eks';
      label = 'Amazon EKS';
    } else if (text.includes('ecs') || text.includes('fargate')) {
      serviceType = 'container';
      icon = 'sailing';
      slug = 'aws-ecs';
      label = 'Amazon ECS';
    } else {
      slug = 'aws-ec2';
      label = 'Amazon EC2';
    }
  } else if (['aks', 'azure', 'container apps'].some((token) => text.includes(token))) {
    parent = 'azure';
    serviceType = text.includes('aks') || text.includes('kubernetes') ? 'kubernetes' : 'container';
    slug = serviceType === 'kubernetes' ? 'azure-aks' : 'azure-container-apps';
    label = serviceType === 'kubernetes' ? 'Azure AKS' : 'Azure Container Apps';
    icon = serviceType === 'kubernetes' ? 'hub' : 'view_in_ar';
  } else if (['cloudflare', 'workers', 'tunnel'].some((token) => text.includes(token))) {
    parent = 'cloudflare';
    icon = 'cyclone';
    serviceType = 'paas';
    runnerType = 'script';
    slug = text.includes('worker') ? 'cloudflare-workers' : 'cloudflare-tunnel';
    label = text.includes('worker') ? 'Cloudflare Workers' : 'Cloudflare Tunnel';
  } else if (text.includes('digitalocean') || text.includes('droplet')) {
    parent = 'digitalocean';
    icon = 'water_drop';
    slug = 'digitalocean-droplet';
    label = 'DigitalOcean Droplets';
  } else if (text.includes('hetzner')) {
    parent = 'hetzner';
    icon = 'dns';
    slug = 'hetzner-server';
    label = 'Hetzner Cloud Server';
  }

  const { credentialsSchema, deploymentConfigSchema } = heuristicPluginSchemas({
    parentCloud: parent,
    serviceType,
    pluginId: slug,
    label,
    prompt,
  });

  return {
    id: slug.slice(0, 64),
    label,
    version: SEMVER,
    category: 'cloud-provider',
    description: prompt.trim().slice(0, 240),
    icon,
    ...(parent ? { parentCloud: parent } : {}),
    runner: { type: runnerType, bundlePath: parent || slug },
    capabilities: {
      serviceType,
      supportsTtl: true,
      supportsCustomDns: true,
      supportsEphemeralDb: false,
    },
    credentialsSchema,
    deploymentConfigSchema,
  };
}
