import { describe, expect, it } from 'vitest'
import type {
  RunningInstanceConfig,
  WorkloadDependenciesConfig,
} from '~/types/provisioning'
import {
  applyDerivedCloudServices,
  deriveCloudServiceResources,
} from '~/utils/deriveCloudServices'

function deps(overrides: Partial<Record<string, { enabled: boolean; placement: string }>> = {}): WorkloadDependenciesConfig {
  const base = { enabled: false, placement: 'in_cluster' as const, connection_url: null }
  return {
    postgres: { ...base },
    mysql: { ...base },
    mongodb: { ...base },
    redis: { ...base },
    kafka: { ...base },
    rabbitmq: { ...base },
    ...(overrides as object),
  } as WorkloadDependenciesConfig
}

const vmInstance: RunningInstanceConfig = { kind: 'vm' } as RunningInstanceConfig
const serverlessInstance: RunningInstanceConfig = { kind: 'serverless' } as RunningInstanceConfig

describe('deriveCloudServiceResources', () => {
  it('enables the managed cluster for a kubernetes runtime on GCP', () => {
    const r = deriveCloudServiceResources({ provider: 'gcp', runtimeMode: 'kubernetes' })
    expect(r.gke).toBe(true)
    expect(r.cloud_run).toBe(false)
    expect(r.compute_instance).toBe(false)
    expect(r.artifact_registry).toBe(true)
  })

  it('enables Compute Engine (VM) and disables the cluster for an instance VM target', () => {
    const r = deriveCloudServiceResources({
      provider: 'gcp',
      runtimeMode: 'running_instance',
      runningInstance: vmInstance,
    })
    expect(r.gke).toBe(false)
    expect(r.compute_instance).toBe(true)
    expect(r.cloud_run).toBe(false)
  })

  it('enables serverless for an instance serverless target on AWS', () => {
    const r = deriveCloudServiceResources({
      provider: 'aws',
      runtimeMode: 'running_instance',
      runningInstance: serverlessInstance,
    })
    expect(r.app_runner).toBe(true)
    expect(r.ec2).toBe(false)
    expect(r.eks).toBe(false)
  })

  it('maps managed Postgres to Cloud SQL (GCP) with engine', () => {
    const r = deriveCloudServiceResources({
      provider: 'gcp',
      runtimeMode: 'kubernetes',
      dependencies: deps({ postgres: { enabled: true, placement: 'managed' } }),
    })
    expect(r.cloud_sql).toBe(true)
    expect(r.cloud_sql_engine).toBe('postgres')
  })

  it('maps managed Redis to ElastiCache (AWS)', () => {
    const r = deriveCloudServiceResources({
      provider: 'aws',
      runtimeMode: 'kubernetes',
      dependencies: deps({ redis: { enabled: true, placement: 'managed' } }),
    })
    expect(r.elasticache).toBe(true)
    expect(r.elasticache_engine).toBe('redis')
    expect(r.rds).toBe(false)
  })

  it('does not derive managed services when placement is in_cluster', () => {
    const r = deriveCloudServiceResources({
      provider: 'gcp',
      runtimeMode: 'kubernetes',
      dependencies: deps({ postgres: { enabled: true, placement: 'in_cluster' } }),
    })
    expect(r.cloud_sql).toBe(false)
  })

  it('returns nothing for local and cloudflare (no runtime-derived services)', () => {
    expect(deriveCloudServiceResources({ provider: 'local', runtimeMode: 'kubernetes' })).toEqual({})
    expect(deriveCloudServiceResources({ provider: 'cloudflare', runtimeMode: 'running_instance' })).toEqual({})
  })
})

describe('applyDerivedCloudServices', () => {
  it('sets managed keys but preserves advanced extras', () => {
    const resources: Record<string, unknown> = { cloud_storage: true, pubsub: true, gke: false }
    applyDerivedCloudServices(resources, { provider: 'gcp', runtimeMode: 'kubernetes' })
    // Derived: cluster on, extras untouched.
    expect(resources.gke).toBe(true)
    expect(resources.cloud_storage).toBe(true)
    expect(resources.pubsub).toBe(true)
  })

  it('turns a managed DB service off when placement changes away from managed', () => {
    const resources: Record<string, unknown> = {}
    applyDerivedCloudServices(resources, {
      provider: 'gcp',
      runtimeMode: 'kubernetes',
      dependencies: deps({ postgres: { enabled: true, placement: 'managed' } }),
    })
    expect(resources.cloud_sql).toBe(true)
    // Switch to in-cluster: derivation must disable the managed service.
    applyDerivedCloudServices(resources, {
      provider: 'gcp',
      runtimeMode: 'kubernetes',
      dependencies: deps({ postgres: { enabled: true, placement: 'in_cluster' } }),
    })
    expect(resources.cloud_sql).toBe(false)
  })
})
