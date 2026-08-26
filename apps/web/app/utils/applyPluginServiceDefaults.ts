import type { CloudPluginSelection } from '~/types/cloudPluginSelection'
import type { CloudProvider, WorkspaceRuntimeMode } from '~/types/provisioning'
import { coerceRegionForProvider } from '~/utils/cloudRegions'
import { parentCloudOf } from '~/utils/pluginParentCloud'

type GcpSlice = {
  vpc: boolean
  subnets: boolean
  gke: boolean
  artifact_registry: boolean
  cloud_run: boolean
  compute_instance: boolean
}

type AwsSlice = {
  vpc: boolean
  subnets: boolean
  eks: boolean
  ecr: boolean
  ec2: boolean
  app_runner: boolean
}

type AzureSlice = {
  vnet: boolean
  subnets: boolean
  aks: boolean
  acr: boolean
  container_apps: boolean
}

type CloudflareSlice = {
  workers: boolean
  pages: boolean
  tunnels: boolean
}

type FormLike = {
  provider: CloudProvider
  runtime_mode: WorkspaceRuntimeMode
  gcp: GcpSlice
  aws: AwsSlice
  azure: AzureSlice
  cloudflare: CloudflareSlice
  kubernetes_options?: { image_source?: string }
}

function serviceId(plugin: CloudPluginSelection, parent: string): string {
  const id = (plugin.provider || '').toLowerCase()
  const explicit = (plugin.service || '').toLowerCase()
  if (explicit) return explicit
  return id.replace(new RegExp(`^${parent}-`), '')
}

/** Turn a selected service plugin into the matching workspace resource flags. */
export function applyPluginServiceDefaults(
  form: FormLike,
  plugin: CloudPluginSelection,
  imageSource?: string,
): void {
  const id = plugin.provider
  if (!id) return
  applyPluginRegionDefaults(form, plugin)
  const parent = parentCloudOf({ id, parent_cloud: null })
  const service = serviceId(plugin, parent)
  const external = (imageSource || form.kubernetes_options?.image_source) === 'external'

  if (parent === 'gcp') {
    form.gcp.vpc = true
    form.gcp.subnets = true
    form.gcp.gke = service === 'gke'
    form.gcp.cloud_run = service === 'cloud-run'
    form.gcp.compute_instance = service === 'gce' || service === 'gce-docker'
    form.gcp.artifact_registry = (form.gcp.gke || form.gcp.cloud_run) && !external
    if (form.gcp.compute_instance) {
      form.runtime_mode = 'running_instance'
    } else if (form.gcp.cloud_run) {
      form.runtime_mode = 'running_instance'
    } else {
      form.runtime_mode = 'kubernetes'
    }
    return
  }
  if (parent === 'aws') {
    form.aws.vpc = true
    form.aws.subnets = true
    form.aws.eks = service === 'eks'
    form.aws.ec2 = service === 'ec2' || service === 'ec2-docker'
    form.aws.app_runner = service === 'ecs-fargate'
    form.aws.ecr = (form.aws.eks || form.aws.app_runner) && !external
    form.runtime_mode = form.aws.eks
      ? 'kubernetes'
      : form.aws.ec2 || form.aws.app_runner
        ? 'running_instance'
        : form.runtime_mode
    return
  }
  if (parent === 'azure') {
    form.azure.vnet = true
    form.azure.subnets = true
    form.azure.aks = service === 'aks'
    form.azure.container_apps = service === 'container-apps' || service === 'aci'
    form.azure.acr = (form.azure.aks || form.azure.container_apps) && !external
    form.runtime_mode = form.azure.aks
      ? 'kubernetes'
      : form.azure.container_apps
        ? 'running_instance'
        : form.runtime_mode
    return
  }
  if (parent === 'cloudflare') {
    form.cloudflare.workers = service === 'workers'
    form.cloudflare.pages = service === 'pages'
    form.cloudflare.tunnels = service === 'tunnels' || service === 'tunnel'
  }
}

/** Sync plugin region onto typed cloud resource slices (GCP/AWS/Azure). */
export function applyPluginRegionDefaults(
  form: FormLike & {
    gcp: { region?: string }
    aws: { region?: string }
    azure: { location?: string }
  },
  plugin: CloudPluginSelection,
): void {
  const id = plugin.provider
  if (!id) return
  const parent = parentCloudOf({ id, parent_cloud: null }) as CloudProvider
  const raw = (plugin.region || '').trim()
  if (!raw) return
  const region = coerceRegionForProvider(parent, raw)
  if (parent === 'gcp') form.gcp.region = region
  else if (parent === 'aws') form.aws.region = region
  else if (parent === 'azure') form.azure.location = region
}
