import { describe, expect, it, vi } from 'vitest'
import { syncWorkspaceCicdToPlatform } from '../app/utils/syncWorkspaceCicd'
import {
  detectCicdPlatformFromPaths,
  oppositeCicdFilePaths,
} from '../app/utils/workspaceInfraScaffold'

describe('detectCicdPlatformFromPaths', () => {
  it('detects github and gitlab exclusively', () => {
    expect(detectCicdPlatformFromPaths(['ci/github/workflows/deploy.yml'])).toBe('github')
    expect(detectCicdPlatformFromPaths(['.gitlab-ci.yml', 'ci/gitlab/.gitlab-ci.yml'])).toBe(
      'gitlab',
    )
    expect(detectCicdPlatformFromPaths(['infra/service.yaml'])).toBeNull()
  })

  it('lists opposite platform paths for cleanup', () => {
    const paths = [
      'ci/github/workflows/deploy.yml',
      '.gitlab-ci.yml',
      'ci/gitlab/.gitlab-ci.yml',
      'infra/README.md',
    ]
    expect(oppositeCicdFilePaths('github', paths)).toEqual([
      '.gitlab-ci.yml',
      'ci/gitlab/.gitlab-ci.yml',
    ])
    expect(oppositeCicdFilePaths('gitlab', paths)).toEqual([
      'ci/github/workflows/deploy.yml',
    ])
  })
})

describe('syncWorkspaceCicdToPlatform', () => {
  it('converts gitlab CI to github and deletes gitlab files', async () => {
    const writes: Array<{ path: string; content: string }> = []
    const deletes: string[] = []
    const api = {
      listWorkspaceFiles: vi.fn(async () => [
        { path: '.gitlab-ci.yml', type: 'file' },
        { path: 'ci/gitlab/.gitlab-ci.yml', type: 'file' },
        { path: 'infra/service.yaml', type: 'file' },
      ]),
      readWorkspaceFile: vi.fn(async () => ({
        content: 'stages: [build]\nbuild:\n  script: [echo hi]\n',
      })),
      writeWorkspaceFile: vi.fn(async (_id: string, path: string, content: string) => {
        writes.push({ path, content })
      }),
      deleteWorkspacePath: vi.fn(async (_id: string, path: string) => {
        deletes.push(path)
      }),
    }

    const result = await syncWorkspaceCicdToPlatform(api, 'ws-1', 'github', {
      appName: 'paygo',
    })

    expect(result.converted).toBe(true)
    expect(writes.some((w) => w.path.startsWith('ci/github/workflows/'))).toBe(true)
    expect(deletes).toEqual(expect.arrayContaining(['.gitlab-ci.yml', 'ci/gitlab/.gitlab-ci.yml']))
  })

  it('converts github Actions to gitlab and deletes github files', async () => {
    const writes: Array<{ path: string }> = []
    const deletes: string[] = []
    const api = {
      listWorkspaceFiles: vi.fn(async () => [
        { path: 'ci/github/workflows/deploy.yml', type: 'file' },
        { path: 'infra/service.yaml', type: 'file' },
      ]),
      readWorkspaceFile: vi.fn(async () => ({
        content: 'name: deploy\non:\n  push:\n    branches: [main]\n',
      })),
      writeWorkspaceFile: vi.fn(async (_id: string, path: string) => {
        writes.push({ path })
      }),
      deleteWorkspacePath: vi.fn(async (_id: string, path: string) => {
        deletes.push(path)
      }),
    }

    const result = await syncWorkspaceCicdToPlatform(api, 'ws-1', 'gitlab', {
      appName: 'paygo',
    })

    expect(result.converted).toBe(true)
    expect(writes.some((w) => w.path === '.gitlab-ci.yml')).toBe(true)
    expect(deletes).toContain('ci/github/workflows/deploy.yml')
  })
})
