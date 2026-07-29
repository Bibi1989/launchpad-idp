import type {
  IaCBundleSummary,
  IaCEngine,
  WorkspaceArtifactsMode,
  WorkspaceListItem,
} from '~/types/provisioning'

type WorkspaceDisplaySource = Pick<IaCBundleSummary, 'engine' | 'provider' | 'artifact_mode' | 'files' | 'status'>
  | Pick<WorkspaceListItem, 'engine' | 'provider' | 'artifact_mode' | 'status'>

function hasIacFiles(files: string[] | undefined): boolean {
  if (!files?.length) return false
  return files.some(
    (path) =>
      path.includes('/terraform/')
      || path.includes('/pulumi/')
      || path.endsWith('.tf')
      || path.endsWith('Pulumi.yaml')
      || path === 'index.ts',
  )
}

function hasManifestFiles(files: string[] | undefined): boolean {
  if (!files?.length) return false
  return files.some(
    (path) => path.includes('/k8s/') || path.includes('/helm/'),
  )
}

export interface WorkspaceStackParts {
  stack: string
  provider: string
  status: string | null
}

/** Stack / provider / status parts for workspace headers. */
export function workspaceStackParts(source: WorkspaceDisplaySource): WorkspaceStackParts {
  const mode = source.artifact_mode
  const files = 'files' in source ? source.files : undefined
  const engine = String(source.engine || '') as IaCEngine | string
  const provider = source.provider || 'local'
  const status = ('status' in source && source.status) ? String(source.status) : null

  let stack: string
  if (mode === 'manifest_only' || (files && hasManifestFiles(files) && !hasIacFiles(files))) {
    stack = 'k8s'
  } else if (mode === 'both' || (files && hasIacFiles(files) && hasManifestFiles(files))) {
    stack = `${engine || 'iac'} + k8s`
  } else if (mode === 'iac_only' || (files && hasIacFiles(files))) {
    stack = engine || 'iac'
  } else if (engine && mode !== 'manifest_only') {
    stack = engine
  } else {
    stack = 'k8s'
  }

  return { stack, provider, status }
}

/** Human label for the IaC / packaging stack shown in workspace headers. */
export function workspaceStackLabel(source: WorkspaceDisplaySource): string {
  const { stack, provider, status } = workspaceStackParts(source)
  const parts = [stack, provider]
  if (status) parts.push(status)
  return parts.join(' · ')
}

export function artifactModeLabel(mode: WorkspaceArtifactsMode | string | null | undefined): string {
  if (mode === 'manifest_only') return 'manifests only'
  if (mode === 'both') return 'iac + manifests'
  return 'iac only'
}
