import { describe, expect, it, vi } from 'vitest'
import {
  enhanceExistingWorkspaceFiles,
  enhanceScaffoldTargets,
  isEnhanceableScaffoldPath,
} from '~/utils/workspaceRepoScaffold'

describe('scaffold AI enhance (ci, docker, terraform)', () => {
  it('detects enhanceable paths for docker, cicd, and iac', () => {
    expect(isEnhanceableScaffoldPath('dockers/Dockerfile.app')).toBe(true)
    expect(isEnhanceableScaffoldPath('ci/github/workflows/app.yml')).toBe(true)
    expect(isEnhanceableScaffoldPath('infra/terraform/main.tf')).toBe(true)
    expect(isEnhanceableScaffoldPath('README.md')).toBe(false)
  })

  it('applies improvedContent for docker, cicd, and terraform targets', async () => {
    const analyze = vi.fn(async (_id: string, payload: { path: string; content: string }) => ({
      kind: 'docker' as const,
      summary: 'ok',
      issues: [],
      suggestions: [],
      improvedContent: `${payload.content}\n# enhanced`,
      analysisSource: 'heuristic' as const,
    }))
    const out = await enhanceScaffoldTargets(
      'ws-1',
      [
        { path: 'dockers/Dockerfile.app', content: 'FROM alpine:3.20\n' },
        { path: 'ci/github/workflows/app.yml', content: 'name: ci\n' },
        { path: 'infra/terraform/main.tf', content: 'resource "null_resource" "x" {}\n' },
        { path: 'docs/note.txt', content: 'skip\n' },
      ],
      analyze,
    )
    expect(analyze).toHaveBeenCalledTimes(3)
    expect(out[0]?.content).toContain('# enhanced')
    expect(out[3]?.content).toBe('skip\n')
  })

  it('rewrites existing workspace files when improvedContent differs', async () => {
    const writeWorkspaceFile = vi.fn(async () => undefined)
    const rewritten = await enhanceExistingWorkspaceFiles(
      'ws-1',
      {
        listWorkspaceFiles: async () => [
          { path: 'ci/github/workflows/app.yml', type: 'file' },
          { path: 'infra/terraform/main.tf', type: 'file' },
          { path: 'README.md', type: 'file' },
        ],
        readWorkspaceFile: async (_id, path) => ({
          content: path.endsWith('.tf') ? 'terraform {}\n' : 'name: ci\njobs:\n  x:\n    runs-on: ubuntu-latest\n',
        }),
        writeWorkspaceFile,
        analyzeWorkspaceFile: async (_id, payload) => ({
          kind: payload.path.endsWith('.tf') ? 'iac' as const : 'cicd' as const,
          summary: 'ok',
          issues: [],
          suggestions: [],
          improvedContent: `${payload.content}\n# ai`,
          analysisSource: 'gemini' as const,
        }),
      },
      ['cicd', 'iac'],
    )
    expect(rewritten).toBe(2)
    expect(writeWorkspaceFile).toHaveBeenCalledTimes(2)
  })
})
