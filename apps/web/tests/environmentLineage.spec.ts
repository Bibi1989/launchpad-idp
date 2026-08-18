import { describe, expect, it } from 'vitest'
import type { Environment } from '~/types/environment'
import { groupEnvironmentsByLineage } from '~/utils/environmentLineage'

function env(partial: Partial<Environment> & Pick<Environment, 'id' | 'name'>): Environment {
  return {
    owner_id: 'o',
    workspace_id: null,
    git_branch: 'main',
    git_repo_url: 'https://github.com/acme/shop.git',
    latest_commit_sha: null,
    status: 'RUNNING',
    namespace_name: `ns-${partial.id}`,
    preview_url: null,
    template_id: null,
    ttl_expires_at: null,
    cost_estimate_hourly: '0',
    cost_accrued: '0',
    time_remaining_seconds: 0,
    error_message: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    lifecycle_stage: 'preview',
    promotion_lineage_id: null,
    ...partial,
  }
}

describe('groupEnvironmentsByLineage', () => {
  it('groups by promotion_lineage_id and orders stages', () => {
    const lineage = 'line-1'
    const groups = groupEnvironmentsByLineage([
      env({
        id: 'prod',
        name: 'shop-production',
        lifecycle_stage: 'production',
        promotion_lineage_id: lineage,
        updated_at: '2026-01-03T00:00:00Z',
      }),
      env({
        id: 'prev',
        name: 'shop',
        lifecycle_stage: 'preview',
        promotion_lineage_id: lineage,
      }),
      env({
        id: 'stg',
        name: 'shop-staging',
        lifecycle_stage: 'staging',
        promotion_lineage_id: lineage,
      }),
      env({
        id: 'other',
        name: 'other-app',
        git_repo_url: 'https://github.com/acme/other.git',
        promotion_lineage_id: null,
        // Newer creation date -> this group sorts to the top (latest first).
        created_at: '2026-02-01T00:00:00Z',
        updated_at: '2026-01-04T00:00:00Z',
      }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0]?.title).toBe('other')
    expect(groups[1]?.title).toBe('shop')
    expect(groups[1]?.environments.map((e) => e.lifecycle_stage)).toEqual([
      'preview',
      'staging',
      'production',
    ])
  })

  it('prefers workspace_name over synthetic workspace git URL', () => {
    const wsId = 'cc456bb3-bd51-4caa-a89f-55c43a59bba7'
    const groups = groupEnvironmentsByLineage([
      env({
        id: 'prev',
        name: 'new-instance',
        workspace_id: wsId,
        workspace_name: 'Checkout API',
        git_repo_url: `https://launchpad.local/workspaces/${wsId}`,
        promotion_lineage_id: 'line-ws',
      }),
      env({
        id: 'stg',
        name: 'new-instance-staging',
        workspace_id: wsId,
        workspace_name: 'Checkout API',
        git_repo_url: `https://launchpad.local/workspaces/${wsId}`,
        lifecycle_stage: 'staging',
        promotion_lineage_id: 'line-ws',
      }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0]?.title).toBe('Checkout API')
    expect(groups[0]?.subtitle).toBeNull()
  })
})
