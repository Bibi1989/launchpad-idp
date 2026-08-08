import type {
  WorkspaceRuntimeMode,
  WorkspaceWizardConfig,
  WorkloadDependenciesConfig,
} from '~/types/provisioning'

export type PreviewDeployMode = 'preview' | 'manifest' | 'compose' | 'attach'

export type PreviewDeployPlan = {
  deploy_mode: PreviewDeployMode
  runtime_mode: WorkspaceRuntimeMode
  enable_postgres: boolean
  enable_redis: boolean
  skip_local_cluster: boolean
  reason: string
  manifest_packaging: string | null
  attach_kind: string | null
  attach_host: string | null
  attach_service: string | null
}

function depsWantPostgres(deps: WorkloadDependenciesConfig): boolean {
  return Boolean(deps.postgres.enabled || deps.mysql.enabled || deps.mongodb.enabled)
}

function depsWantRedis(deps: WorkloadDependenciesConfig): boolean {
  return Boolean(deps.redis.enabled)
}

/**
 * Client-side mirror of ``app.services.preview_deploy_plan.resolve_preview_deploy_plan``.
 */
export function resolvePreviewDeployPlan(
  config: WorkspaceWizardConfig,
  requestedDeployMode: PreviewDeployMode | null = null,
): PreviewDeployPlan {
  const runtime = config.runtime_mode ?? 'kubernetes'
  const enablePostgres = depsWantPostgres(config.dependencies)
  const enableRedis = depsWantRedis(config.dependencies)
  const packaging =
    config.kubernetes_packaging !== 'none' ? config.kubernetes_packaging : null

  if (runtime === 'docker_compose') {
    return {
      deploy_mode: 'compose',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: true,
      reason: 'Workspace runtime_mode=docker_compose (local Compose preview)',
      manifest_packaging: null,
      attach_kind: null,
      attach_host: null,
      attach_service: null,
    }
  }

  if (runtime === 'running_instance') {
    const instance = config.running_instance ?? {
      kind: 'local_machine' as const,
      host: null,
      service_name: null,
    }
    return {
      deploy_mode: 'attach',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: true,
      reason: `Workspace runtime_mode=running_instance (${instance.kind})`,
      manifest_packaging: null,
      attach_kind: instance.kind,
      attach_host: instance.host ?? null,
      attach_service: instance.service_name ?? null,
    }
  }

  if (requestedDeployMode === 'compose') {
    return {
      deploy_mode: 'compose',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: true,
      reason: 'Client requested compose deploy',
      manifest_packaging: null,
      attach_kind: null,
      attach_host: null,
      attach_service: null,
    }
  }

  if (requestedDeployMode === 'attach') {
    const instance = config.running_instance ?? {
      kind: 'local_machine' as const,
      host: null,
      service_name: null,
    }
    return {
      deploy_mode: 'attach',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: true,
      reason: 'Client requested running-instance deploy',
      manifest_packaging: null,
      attach_kind: instance.kind,
      attach_host: instance.host ?? null,
      attach_service: instance.service_name ?? null,
    }
  }

  if (requestedDeployMode === 'manifest') {
    return {
      deploy_mode: 'manifest',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: false,
      reason: 'Client requested manifest deploy',
      manifest_packaging: packaging,
      attach_kind: null,
      attach_host: null,
      attach_service: null,
    }
  }

  if (requestedDeployMode === 'preview') {
    return {
      deploy_mode: 'preview',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: false,
      reason: 'Client requested control-plane preview deploy',
      manifest_packaging: packaging,
      attach_kind: null,
      attach_host: null,
      attach_service: null,
    }
  }

  if (
    config.kubernetes_packaging === 'raw_manifests'
    || config.kubernetes_packaging === 'helm'
  ) {
    return {
      deploy_mode: 'manifest',
      runtime_mode: runtime,
      enable_postgres: enablePostgres,
      enable_redis: enableRedis,
      skip_local_cluster: false,
      reason: 'Workspace has Kubernetes packaging; using manifest deploy',
      manifest_packaging: packaging,
      attach_kind: null,
      attach_host: null,
      attach_service: null,
    }
  }

  return {
    deploy_mode: 'preview',
    runtime_mode: runtime,
    enable_postgres: enablePostgres,
    enable_redis: enableRedis,
    skip_local_cluster: false,
    reason: 'Default control-plane preview deploy',
    manifest_packaging: packaging,
    attach_kind: null,
    attach_host: null,
    attach_service: null,
  }
}
