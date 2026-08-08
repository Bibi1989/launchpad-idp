import { describe, expect, it } from 'vitest'
import { hasKubernetesClusterService } from '../app/utils/cloudServiceOptions'
import { gcpResourcesSchema, awsResourcesSchema } from '../app/utils/cloudValidation'

describe('hasKubernetesClusterService', () => {
  it('requires GKE on GCP (not Cloud Run alone)', () => {
    expect(hasKubernetesClusterService('gcp', { gke: false, cloud_run: true })).toBe(false)
    expect(hasKubernetesClusterService('gcp', { gke: true })).toBe(true)
  })

  it('requires EKS on AWS and AKS on Azure', () => {
    expect(hasKubernetesClusterService('aws', { eks: false })).toBe(false)
    expect(hasKubernetesClusterService('aws', { eks: true })).toBe(true)
    expect(hasKubernetesClusterService('azure', { aks: false, container_apps: true })).toBe(false)
    expect(hasKubernetesClusterService('azure', { aks: true })).toBe(true)
  })

  it('treats local as always available', () => {
    expect(hasKubernetesClusterService('local', {})).toBe(true)
  })
})

describe('sql / cache engine schemas', () => {
  it('defaults cloud_sql_engine to postgres and accepts mysql', () => {
    const parsed = gcpResourcesSchema.parse({
      project_id: 'my-project',
      cloud_sql: true,
      cloud_sql_engine: 'mysql',
    })
    expect(parsed.cloud_sql_engine).toBe('mysql')
  })

  it('rejects Cloud SQL mariadb', () => {
    expect(() =>
      gcpResourcesSchema.parse({
        project_id: 'my-project',
        cloud_sql: true,
        cloud_sql_engine: 'mariadb',
      }),
    ).toThrow()
  })

  it('accepts RDS and ElastiCache engines', () => {
    const parsed = awsResourcesSchema.parse({
      rds: true,
      rds_engine: 'mariadb',
      elasticache: true,
      elasticache_engine: 'memcached',
      lambda_fn: true,
      lambda_runtime: 'python3.12',
    })
    expect(parsed.rds_engine).toBe('mariadb')
    expect(parsed.elasticache_engine).toBe('memcached')
    expect(parsed.lambda_runtime).toBe('python3.12')
  })
})
