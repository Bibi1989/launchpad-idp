import { describe, expect, it } from 'vitest'
import { provisioningWizardSchema, githubRepoSchema } from '../app/utils/cloudValidation'

describe('provisioningWizardSchema', () => {
  it('accepts a gcp terraform selection', () => {
    const parsed = provisioningWizardSchema.parse({
      name: 'Demo-Stack',
      iac_engine: 'terraform',
      provider: 'gcp',
      resources: {
        project_id: 'my-project',
        vpc: true,
        subnets: true,
      },
      credentials: {},
      run_init: true,
    })
    expect(parsed.name).toBe('demo-stack')
    expect(parsed.provider).toBe('gcp')
  })

  it('accepts local kind without cloud credentials', () => {
    const parsed = provisioningWizardSchema.parse({
      name: 'kind-demo',
      provider: 'local',
      resources: {
        cluster_name: 'launchpad',
        context: 'kind-launchpad',
      },
      credentials: {},
      run_init: true,
    })
    expect(parsed.provider).toBe('local')
    expect(parsed.kubernetes_packaging).toBe('raw_manifests')
  })

  it('keeps container_scaffold frameworks through parse', () => {
    const parsed = provisioningWizardSchema.parse({
      name: 'multi-stack',
      provider: 'local',
      resources: {
        cluster_name: 'launchpad',
        context: 'kind-launchpad',
      },
      credentials: {},
      container_scaffold: {
        enabled: true,
        generate_dockerfile: true,
        generate_docker_compose: true,
        stack: 'nuxtjs',
        frameworks: ['nuxtjs', 'fastapi', 'nestjs'],
        app_name: 'shop',
        listen_port: 3000,
      },
    })
    expect(parsed.container_scaffold.enabled).toBe(true)
    expect(parsed.container_scaffold.frameworks).toEqual(['nuxtjs', 'fastapi', 'nestjs'])
    expect(parsed.container_scaffold.stack).toBe('nuxtjs')
  })

  it('requires zone_name for cloudflare dns', () => {
    const result = provisioningWizardSchema.safeParse({
      name: 'cf-stack',
      provider: 'cloudflare',
      resources: {
        account_id: 'abc123456789',
        dns_records: true,
      },
    })
    expect(result.success).toBe(false)
  })
})

describe('githubRepoSchema', () => {
  it('accepts a valid repo request with installation', () => {
    const parsed = githubRepoSchema.parse({
      name: 'launchpad-demo',
      installation_id: 42,
      private: true,
    })
    expect(parsed.name).toBe('launchpad-demo')
    expect(parsed.installation_id).toBe(42)
    expect(parsed.include_workflow).toBe(true)
    expect(parsed.include_dockerfiles).toBe(true)
  })

  it('allows missing installation when API resolves default', () => {
    const parsed = githubRepoSchema.parse({
      name: 'launchpad-demo',
      private: true,
    })
    expect(parsed.installation_id == null || parsed.installation_id === undefined).toBe(true)
  })

  it('accepts existing repo import with workflow opt-out', () => {
    const parsed = githubRepoSchema.parse({
      name: 'demo',
      installation_id: 7,
      existing_full_name: 'acme/demo',
      include_workflow: false,
      include_dockerfiles: false,
    })
    expect(parsed.existing_full_name).toBe('acme/demo')
    expect(parsed.include_workflow).toBe(false)
    expect(parsed.include_dockerfiles).toBe(false)
  })
})
