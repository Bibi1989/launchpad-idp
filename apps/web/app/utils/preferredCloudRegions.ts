import type { UserCloudCredentialsStatus } from '~/types/userCredentials'

const DEFAULT_GCP_REGION = 'us-central1'
const DEFAULT_AWS_REGION = 'us-east-1'
const DEFAULT_AZURE_LOCATION = 'eastus'

/** Apply vault-preferred regions onto wizard form slices (mutates targets). */
export function applyPreferredCloudRegions(
  status: UserCloudCredentialsStatus | null | undefined,
  targets: {
    gcp?: { region?: string; project_id?: string }
    aws?: { region?: string }
    azure?: { location?: string }
    running_instance?: { region?: string | null }
  },
  options?: { overwrite?: boolean },
): void {
  if (!status) return
  const overwrite = options?.overwrite === true
  const gcpRegion = (status.gcp_region || '').trim()
  const awsRegion = (status.aws_region || '').trim()
  const azureLocation = (status.azure_location || '').trim()
  const gcpProject = (status.gcp_project_id || '').trim()

  if (targets.gcp) {
    const current = (targets.gcp.region || '').trim()
    if (gcpRegion && (overwrite || !current || current === DEFAULT_GCP_REGION)) {
      targets.gcp.region = gcpRegion
    }
    if (gcpProject && !(targets.gcp.project_id || '').trim()) {
      targets.gcp.project_id = gcpProject
    }
  }
  if (targets.aws) {
    const current = (targets.aws.region || '').trim()
    if (awsRegion && (overwrite || !current || current === DEFAULT_AWS_REGION)) {
      targets.aws.region = awsRegion
    }
  }
  if (targets.azure) {
    const current = (targets.azure.location || '').trim()
    if (azureLocation && (overwrite || !current || current === DEFAULT_AZURE_LOCATION)) {
      targets.azure.location = azureLocation
    }
  }

  if (targets.running_instance) {
    const preferred = gcpRegion || awsRegion || azureLocation
    if (preferred && !(targets.running_instance.region || '').trim()) {
      targets.running_instance.region = preferred
    }
  }
}
