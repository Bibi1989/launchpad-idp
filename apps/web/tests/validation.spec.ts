import { describe, expect, it } from 'vitest'
import { environmentCreateSchema } from '../app/utils/validation'
import { loginSchema, registerSchema } from '../app/utils/authValidation'

describe('environmentCreateSchema', () => {
  it('accepts a valid payload', () => {
    const parsed = environmentCreateSchema.parse({
      name: 'Demo-Env',
      git_branch: 'feature/demo',
      git_repo_url: 'https://github.com/acme/demo.git',
      ttl_hours: '24',
      workspace_id: null,
    })

    expect(parsed.name).toBe('demo-env')
    expect(parsed.git_branch).toBe('feature/demo')
    expect(parsed.git_repo_url).toBe('https://github.com/acme/demo.git')
    expect(parsed.ttl_hours).toBe(24)
  })

  it('rejects invalid names', () => {
    const result = environmentCreateSchema.safeParse({
      name: '1bad',
      git_branch: 'main',
      git_repo_url: 'https://github.com/acme/demo.git',
      ttl_hours: 24,
    })

    expect(result.success).toBe(false)
  })

  it('rejects invalid repo urls', () => {
    const result = environmentCreateSchema.safeParse({
      name: 'demo-env',
      git_branch: 'main',
      git_repo_url: 'not-a-url',
      ttl_hours: 24,
    })
    expect(result.success).toBe(false)
  })
})

describe('auth schemas', () => {
  it('accepts login input', () => {
    const parsed = loginSchema.parse({
      email: 'dev@launchpad.local',
      password: 'secret',
    })
    expect(parsed.email).toBe('dev@launchpad.local')
  })

  it('rejects short register passwords', () => {
    const result = registerSchema.safeParse({
      email: 'a@b.com',
      password: 'short',
      display_name: 'A',
    })
    expect(result.success).toBe(false)
  })
})
