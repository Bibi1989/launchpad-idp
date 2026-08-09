import { describe, expect, it } from 'vitest'
import type { Environment } from '~/types/environment'
import {
  resolvePreviewEndpoints,
  secondaryPreviewEndpoints,
} from '~/utils/previewEndpoints'

function env(partial: Partial<Environment>): Environment {
  return {
    id: 'e1',
    owner_id: 'o1',
    workspace_id: null,
    name: 'demo',
    git_branch: 'main',
    git_repo_url: 'https://example.com/r.git',
    latest_commit_sha: null,
    status: 'RUNNING',
    namespace_name: 'ns',
    preview_url: null,
    template_id: null,
    ttl_expires_at: new Date().toISOString(),
    cost_estimate_hourly: '0',
    cost_accrued: '0',
    time_remaining_seconds: 0,
    error_message: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...partial,
  }
}

describe('previewEndpoints', () => {
  it('uses preview_endpoints and keeps frontend for open app', () => {
    const e = env({
      preview_url: 'http://127.0.0.1:8090',
      node_port: 8090,
      preview_endpoints: [
        {
          name: 'web-ui',
          app_kind: 'frontend',
          url: 'http://127.0.0.1:8090',
          port: 8090,
        },
        {
          name: 'api-server',
          app_kind: 'backend',
          url: 'http://127.0.0.1:8080',
          port: 8080,
        },
      ],
    })
    expect(resolvePreviewEndpoints(e)).toHaveLength(2)
    expect(secondaryPreviewEndpoints(e).map((x) => x.name)).toEqual(['api-server'])
  })

  it('falls back to single preview_url', () => {
    const e = env({ preview_url: 'http://127.0.0.1:3000', node_port: 3000 })
    expect(resolvePreviewEndpoints(e)).toEqual([
      {
        name: 'app',
        app_kind: 'frontend',
        url: 'http://127.0.0.1:3000',
        port: 3000,
        exposed: true,
      },
    ])
    expect(secondaryPreviewEndpoints(e)).toEqual([])
  })
})
