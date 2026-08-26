import { describe, expect, it } from 'vitest'
import { PLUGIN_BOILERPLATES } from '../app/utils/pluginManifestBoilerplates'
import {
  compilePluginManifest,
  hydratePluginForm,
  pluginSchemaGeneratePayload,
  slugifyPluginId,
  validateCompiledManifest,
} from '../app/utils/pluginManifestForm'
import { dumpStructured, detectStructuredFormat, parseStructured } from '../app/utils/yamlJson'
import { parentCloudOf, pluginIsConnected } from '../app/utils/pluginParentCloud'

describe('plugin manifest form', () => {
  it('slugifies ids to kebab-case', () => {
    expect(slugifyPluginId('DigitalOcean Droplets')).toBe('digitalocean-droplets')
    expect(slugifyPluginId('  AWS ECS  ')).toBe('aws-ecs')
  })

  it('compiles a valid DigitalOcean boilerplate', () => {
    const preset = PLUGIN_BOILERPLATES.find((item) => item.id === 'digitalocean-droplets')
    expect(preset).toBeTruthy()
    const payload = compilePluginManifest(preset!.form)
    expect(payload.id).toBe('digitalocean-droplet')
    expect(payload.runner.type).toBe('terraform')
    expect(payload.runner.bundlePath).toBe('digitalocean')
    expect(payload.capabilities.serviceType).toBe('vm')
    expect(payload.credential_fields?.some((field) => field.name === 'token')).toBe(true)
    expect(validateCompiledManifest(payload)).toEqual([])
  })

  it('round-trips JSON Schema through YAML', () => {
    const schema = {
      type: 'object',
      required: ['token'],
      properties: {
        token: { type: 'string', title: 'API Token', writeOnly: true },
      },
    }
    const yaml = dumpStructured(schema, 'yaml')
    const parsed = parseStructured(yaml, 'yaml')
    expect(parsed.error).toBeNull()
    expect(parsed.value).toEqual(schema)
  })

  it('reports JSON syntax errors with a line number', () => {
    const parsed = parseStructured('{\n  "type":\n}', 'json')
    expect(parsed.value).toBeNull()
    expect(parsed.error?.line).toBeGreaterThan(0)
  })

  it('hydrates camelCase and snake_case manifests', () => {
    const form = hydratePluginForm({
      displayName: 'Railway PaaS',
      runner: { engine: 'node', entry: 'RailwayProvider' },
      capabilities: { serviceType: 'paas', supportsTtl: true },
      credentials_schema: { type: 'object' },
    })
    expect(form.id).toBe('railway-paas')
    expect(form.runnerType).toBe('node')
    expect(form.runnerTarget).toBe('RailwayProvider')
    expect(form.serviceType).toBe('paas')
    expect(form.supportsTtl).toBe(true)
  })

  it('hydrates owner and visibility', () => {
    const form = hydratePluginForm({
      id: 'do',
      label: 'DO',
      owner: 'user',
      visibility: 'public',
    })
    expect(form.owner).toBe('user')
    expect(form.visibility).toBe('public')
  })

  it('compiles optional parentCloud and keywords', () => {
    const form = hydratePluginForm({
      id: 'gcp-gke-custom',
      label: 'My GKE',
      version: '1.0.0',
      category: 'cloud-provider',
      description: 'GKE via Terraform',
      parentCloud: 'gcp',
      keywords: ['gke', 'gcp'],
      runner: { type: 'terraform', bundlePath: 'gcp' },
      capabilities: { serviceType: 'kubernetes', supportsTtl: true, supportsCustomDns: true, supportsEphemeralDb: false },
    })
    const payload = compilePluginManifest(form)
    expect(payload.parentCloud).toBe('gcp')
    expect(payload.keywords).toEqual(['gke', 'gcp'])
    expect(validateCompiledManifest(payload)).toEqual([])
  })

  it('detects YAML vs JSON on paste', () => {
    expect(detectStructuredFormat('{\n  "type": "object"\n}')).toBe('json')
    expect(detectStructuredFormat('type: object\nproperties:\n  region:\n    type: string\n')).toBe('yaml')
  })

  it('treats GKE as connected when GCP settings keys exist', () => {
    expect(parentCloudOf({ id: 'gcp-gke', parent_cloud: 'gcp' })).toBe('gcp')
    expect(
      pluginIsConnected({ id: 'gcp-gke', parent_cloud: 'gcp' }, { has_gcp: true, has_aws: false, has_azure: false, has_cloudflare: false }, {}),
    ).toBe(true)
  })

  it('builds a generate-schemas payload from the form', () => {
    const preset = PLUGIN_BOILERPLATES.find((item) => item.id === 'aws-ecs')
    expect(preset).toBeTruthy()
    const payload = pluginSchemaGeneratePayload(preset!.form, 'use Fargate')
    expect(payload.parent_cloud).toBe('aws')
    expect(payload.service_type).toBe('container')
    expect(payload.plugin_id).toBe('aws-ecs')
    expect(payload.prompt).toBe('use Fargate')
  })
})
