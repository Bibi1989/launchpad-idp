import type { RuntimeTarget } from '~/types/cloudProviders'
import type { WorkspaceWizardConfig } from '~/types/provisioning'
import type { PreviewDeployPlan } from '~/utils/previewDeployPlan'

/** Restrict launch cloud-plugin tiles to runtimes that match the linked workspace. */
export function allowedRuntimeTargetsForLaunch(
  plan: PreviewDeployPlan | null,
  wizard: WorkspaceWizardConfig | null,
): RuntimeTarget[] | null {
  if (!plan || !wizard || wizard.cloud.provider === 'local') return null

  if (plan.deploy_mode === 'compose') {
    return ['docker_host']
  }

  if (plan.deploy_mode === 'attach') {
    const kind = plan.attach_kind
    if (kind === 'vm') return ['vm', 'docker_host']
    if (kind === 'serverless') return ['paas']
    if (kind === 'local_machine') return null
    return ['vm', 'docker_host', 'paas']
  }

  if (
    plan.runtime_mode === 'kubernetes'
    || plan.deploy_mode === 'manifest'
    || plan.deploy_mode === 'preview'
  ) {
    return ['kubernetes']
  }

  return null
}
