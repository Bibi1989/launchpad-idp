import type { PluginManifestForm } from '~/types/pluginManifest'
import { defaultPluginForm } from '~/utils/pluginManifestForm'

export interface PluginBoilerplate {
  id: string
  label: string
  form: PluginManifestForm
}

const DO_CREDENTIALS: Record<string, unknown> = {
  $schema: 'http://json-schema.org/draft-07/schema#',
  type: 'object',
  additionalProperties: false,
  required: ['token'],
  properties: {
    token: {
      type: 'string',
      title: 'API Token',
      description: 'DigitalOcean personal access token with write scope.',
      writeOnly: true,
    },
  },
}

const DO_DEPLOY: Record<string, unknown> = {
  $schema: 'http://json-schema.org/draft-07/schema#',
  type: 'object',
  additionalProperties: false,
  required: ['region', 'size'],
  properties: {
    region: { type: 'string', title: 'Region', default: 'nyc3' },
    size: { type: 'string', title: 'Droplet size', default: 's-1vcpu-2gb' },
    image: { type: 'string', title: 'Image slug', default: 'ubuntu-24-04-x64' },
  },
}

function digitalOcean(): PluginManifestForm {
  const form = defaultPluginForm()
  form.parentCloud = 'digitalocean'
  form.credentialsSchema = DO_CREDENTIALS
  form.deploymentConfigSchema = DO_DEPLOY
  return form
}

function awsEcs(): PluginManifestForm {
  return {
    id: 'aws-ecs',
    label: 'AWS ECS',
    version: '1.0.0',
    category: 'cloud-provider',
    description: 'Run containers on Amazon ECS with Terraform.',
    icon: 'cloud_upload',
    owner: 'user',
    visibility: 'private',
    parentCloud: 'aws',
    runnerType: 'terraform',
    runnerTarget: 'aws-ecs',
    serviceType: 'container',
    supportsTtl: true,
    supportsCustomDns: true,
    supportsEphemeralDb: true,
    credentialsSchema: {
      $schema: 'http://json-schema.org/draft-07/schema#',
      type: 'object',
      additionalProperties: false,
      properties: {},
      description: 'Reuses AWS keys from Settings.',
    },
    deploymentConfigSchema: {
      $schema: 'http://json-schema.org/draft-07/schema#',
      type: 'object',
      additionalProperties: false,
      required: ['cluster', 'image'],
      properties: {
        cluster: { type: 'string', title: 'ECS cluster name' },
        image: { type: 'string', title: 'Container image' },
        cpu: { type: 'integer', title: 'Task CPU units', default: 256 },
        memory: { type: 'integer', title: 'Task memory (MiB)', default: 512 },
      },
    },
  }
}

function railwayPaas(): PluginManifestForm {
  return {
    id: 'railway-paas',
    label: 'Railway PaaS',
    version: '1.0.0',
    category: 'cloud-provider',
    description: 'Deploy a service on Railway from a Git repository.',
    icon: 'rocket_launch',
    owner: 'user',
    visibility: 'private',
    runnerType: 'node',
    runnerTarget: 'RailwayProvider',
    serviceType: 'paas',
    supportsTtl: true,
    supportsCustomDns: true,
    supportsEphemeralDb: true,
    credentialsSchema: {
      $schema: 'http://json-schema.org/draft-07/schema#',
      type: 'object',
      additionalProperties: false,
      required: ['token'],
      properties: {
        token: { type: 'string', title: 'Railway API token', writeOnly: true },
      },
    },
    deploymentConfigSchema: {
      $schema: 'http://json-schema.org/draft-07/schema#',
      type: 'object',
      additionalProperties: false,
      required: ['project'],
      properties: {
        project: { type: 'string', title: 'Project name' },
        environment: { type: 'string', title: 'Environment', default: 'production' },
        startCommand: { type: 'string', title: 'Start command' },
      },
    },
  }
}

function cloudflareIngress(): PluginManifestForm {
  return {
    id: 'cloudflare-ingress',
    label: 'Cloudflare Ingress',
    version: '1.0.0',
    category: 'ingress',
    description: 'Expose a service through a Cloudflare tunnel.',
    icon: 'cyclone',
    owner: 'user',
    visibility: 'private',
    parentCloud: 'cloudflare',
    runnerType: 'script',
    runnerTarget: 'cloudflare-tunnel',
    serviceType: 'container',
    supportsTtl: false,
    supportsCustomDns: true,
    supportsEphemeralDb: false,
    credentialsSchema: {
      $schema: 'http://json-schema.org/draft-07/schema#',
      type: 'object',
      additionalProperties: false,
      properties: {},
      description: 'Reuses the Cloudflare API token from Settings.',
    },
    deploymentConfigSchema: {
      $schema: 'http://json-schema.org/draft-07/schema#',
      type: 'object',
      additionalProperties: false,
      required: ['hostname'],
      properties: {
        hostname: { type: 'string', title: 'Public hostname' },
        serviceUrl: { type: 'string', title: 'Origin service URL', default: 'http://localhost:8080' },
        accountId: { type: 'string', title: 'Account ID' },
      },
    },
  }
}

export const PLUGIN_BOILERPLATES: PluginBoilerplate[] = [
  { id: 'digitalocean-droplets', label: 'DigitalOcean Droplets', form: digitalOcean() },
  { id: 'aws-ecs', label: 'AWS ECS', form: awsEcs() },
  { id: 'railway-paas', label: 'Railway PaaS', form: railwayPaas() },
  { id: 'cloudflare-ingress', label: 'Cloudflare Ingress', form: cloudflareIngress() },
]

export function boilerplateById(id: string): PluginBoilerplate | undefined {
  return PLUGIN_BOILERPLATES.find((item) => item.id === id)
}

export function cloneForm(form: PluginManifestForm): PluginManifestForm {
  return {
    ...form,
    icon: form.icon || 'cloud',
    owner: form.owner === 'organization' ? 'organization' : 'user',
    visibility: form.visibility === 'public' ? 'public' : 'private',
    docsUrl: form.docsUrl ?? '',
    homepage: form.homepage ?? '',
    license: form.license ?? '',
    author: form.author ?? '',
    keywords: [...(form.keywords ?? [])],
    parentCloud: form.parentCloud ?? '',
    useStackRunners: form.useStackRunners ?? false,
    provisionRunnerType: form.provisionRunnerType ?? form.runnerType,
    provisionRunnerTarget: form.provisionRunnerTarget ?? form.runnerTarget,
    configRunnerType: form.configRunnerType ?? 'ansible',
    configRunnerTarget: form.configRunnerTarget ?? 'config/site.yml',
    defaultIacEngine: form.defaultIacEngine ?? 'launchpad',
    defaultConfigTool: form.defaultConfigTool ?? 'cloud-init',
    credentialsSchema: structuredClone(form.credentialsSchema),
    deploymentConfigSchema: structuredClone(form.deploymentConfigSchema),
  }
}
