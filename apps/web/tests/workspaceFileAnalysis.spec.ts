import { describe, expect, it } from 'vitest'
import {
  collectAnalyzablePaths,
  isAnalyzableWorkspacePath,
} from '~/utils/workspaceFileAnalysis'

describe('workspaceFileAnalysis paths', () => {
  it('detects analyzable infra paths', () => {
    expect(isAnalyzableWorkspacePath('infra/terraform/modules/vpc/main.tf')).toBe(true)
    expect(isAnalyzableWorkspacePath('dockers/api/Dockerfile')).toBe(true)
    expect(isAnalyzableWorkspacePath('infra/terraform/.terraform/providers/x')).toBe(false)
    expect(isAnalyzableWorkspacePath('README.md')).toBe(false)
  })

  it('collects files under a selected folder', () => {
    const nodes = [
      { path: 'infra', type: 'directory' as const },
      { path: 'infra/terraform', type: 'directory' as const },
      { path: 'infra/terraform/main.tf', type: 'file' as const },
      { path: 'infra/terraform/modules/vpc/main.tf', type: 'file' as const },
      { path: 'infra/terraform/.terraform/lock.hcl', type: 'file' as const },
      { path: 'README.md', type: 'file' as const },
    ]
    const paths = collectAnalyzablePaths(nodes, 'infra/terraform')
    expect(paths).toEqual([
      'infra/terraform/main.tf',
      'infra/terraform/modules/vpc/main.tf',
    ])
  })

  it('returns a single selected file', () => {
    const nodes = [
      { path: 'infra/terraform/main.tf', type: 'file' as const },
    ]
    expect(collectAnalyzablePaths(nodes, 'infra/terraform/main.tf')).toEqual([
      'infra/terraform/main.tf',
    ])
  })
})
