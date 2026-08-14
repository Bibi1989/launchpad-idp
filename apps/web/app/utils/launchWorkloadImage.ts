import type { PreviewDeployPlan } from '~/utils/previewDeployPlan'

/**
 * When launching from a linked workspace, the control plane resolves the image
 * from manifests, Compose, or the workspace Dockerfile. Do not require a
 * manual container image override in that case.
 */
export function launchRequiresWorkloadImage(input: {
  usesWorkspaceSource: boolean
  buildsFromRepo: boolean
  workspaceHasManifests: boolean
  deployMode?: PreviewDeployPlan['deploy_mode'] | null
  kubernetesImageSource?: 'external' | 'build_registry' | null
}): boolean {
  if (input.kubernetesImageSource === 'build_registry') return false
  if (input.usesWorkspaceSource) return false
  if (input.buildsFromRepo) return false
  if (input.workspaceHasManifests) return false
  if (input.deployMode === 'compose' || input.deployMode === 'attach') return false
  return true
}

export function launchShowsWorkloadImageInput(input: {
  usesWorkspaceSource: boolean
  buildsFromRepo: boolean
  workspaceHasManifests: boolean
  deployMode?: PreviewDeployPlan['deploy_mode'] | null
  kubernetesImageSource?: 'external' | 'build_registry' | null
}): boolean {
  if (input.kubernetesImageSource !== 'external') return false
  return launchRequiresWorkloadImage(input)
}
