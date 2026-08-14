import { describe, expect, it } from 'vitest'
import { applyPreferredCloudRegions } from '~/utils/preferredCloudRegions'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'

describe('applyPreferredCloudRegions', () => {
  it('fills default regions from vault status', () => {
    const status: UserCloudCredentialsStatus = {
      has_gcp: true,
      has_aws: true,
      has_azure: true,
      has_cloudflare: false,
      gcp_region: 'europe-west1',
      aws_region: 'eu-central-1',
      azure_location: 'westeurope',
      gcp_project_id: 'my-proj',
    }
    const gcp = { region: 'us-central1', project_id: '' }
    const aws = { region: 'us-east-1' }
    const azure = { location: 'eastus' }
    applyPreferredCloudRegions(status, { gcp, aws, azure })
    expect(gcp.region).toBe('europe-west1')
    expect(gcp.project_id).toBe('my-proj')
    expect(aws.region).toBe('eu-central-1')
    expect(azure.location).toBe('westeurope')
  })

  it('does not overwrite custom regions unless overwrite is set', () => {
    const status: UserCloudCredentialsStatus = {
      has_gcp: true,
      has_aws: false,
      has_azure: false,
      has_cloudflare: false,
      gcp_region: 'europe-west1',
    }
    const gcp = { region: 'asia-east1', project_id: '' }
    applyPreferredCloudRegions(status, { gcp })
    expect(gcp.region).toBe('asia-east1')
    applyPreferredCloudRegions(status, { gcp }, { overwrite: true })
    expect(gcp.region).toBe('europe-west1')
  })
})
