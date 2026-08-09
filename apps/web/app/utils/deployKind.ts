import type { Environment } from '~/types/environment'

export type DeployKind = 'kubernetes' | 'docker' | 'instance'

/** Map persisted deploy_mode to the operator-facing provision kind. */
export function resolveDeployKind(env: Pick<Environment, 'deploy_mode'> | string | null | undefined): DeployKind {
  const mode = typeof env === 'string' || env == null
    ? (env || '')
    : (env.deploy_mode || '')
  switch ((mode || '').toLowerCase()) {
    case 'compose':
      return 'docker'
    case 'attach':
      return 'instance'
    case 'manifest':
    case 'preview':
    default:
      return 'kubernetes'
  }
}
