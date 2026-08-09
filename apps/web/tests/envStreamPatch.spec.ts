import { describe, expect, it } from 'vitest'
import type { Environment } from '~/types/environment'
import { applyEnvStreamPatch, envStreamToPatch } from '~/utils/envStreamPatch'

function baseEnv(overrides: Partial<Environment> = {}): Environment {
  return {
    id: 'env-1',
    owner_id: 'u1',
    workspace_id: null,
    name: 'demo',
    git_branch: 'main',
    git_repo_url: 'https://example.com/repo.git',
    latest_commit_sha: null,
    status: 'PROVISIONING',
    namespace_name: 'ns',
    preview_url: null,
    template_id: null,
    ttl_expires_at: '2099-01-01T00:00:00Z',
    cost_estimate_hourly: '0',
    cost_accrued: '0',
    time_remaining_seconds: 3600,
    error_message: null,
    created_at: '2099-01-01T00:00:00Z',
    updated_at: '2099-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('envStreamPatch', () => {
  it('applies ready preview fields from SSE', () => {
    const env = baseEnv()
    applyEnvStreamPatch(env, {
      type: 'STATUS_CHANGE',
      status: 'RUNNING',
      preview_url: 'http://127.0.0.1:8081',
      node_port: 8081,
      app_ready: true,
      commit_sha: 'abc',
    })
    expect(env.status).toBe('RUNNING')
    expect(env.preview_url).toBe('http://127.0.0.1:8081')
    expect(env.node_port).toBe(8081)
    expect(env.app_ready).toBe(true)
    expect(env.latest_commit_sha).toBe('abc')
  })

  it('builds list patch with failure fields', () => {
    const patch = envStreamToPatch('env-1', {
      type: 'EXECUTION_FAILED',
      status: 'FAILED',
      error_message: 'port busy',
    })
    expect(patch).toMatchObject({
      id: 'env-1',
      status: 'FAILED',
      error_message: 'port busy',
      app_ready: false,
    })
  })
})
