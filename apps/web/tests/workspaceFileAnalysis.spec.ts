import { describe, expect, it } from 'vitest'
import { detectWorkspaceFileKind } from '../app/utils/workspaceFileAnalysis'

describe('detectWorkspaceFileKind', () => {
  it('detects docker paths', () => {
    expect(detectWorkspaceFileKind('dockers/nuxtjs/Dockerfile')).toBe('docker')
    expect(detectWorkspaceFileKind('docker-compose.yml')).toBe('docker')
  })

  it('detects cicd paths', () => {
    expect(detectWorkspaceFileKind('ci/github/workflows/deploy.yml')).toBe('cicd')
    expect(detectWorkspaceFileKind('ci/gitlab/.gitlab-ci.yml')).toBe('cicd')
  })

  it('detects kubernetes and iac paths', () => {
    expect(detectWorkspaceFileKind('infra/k8s/manifests/deployment.yaml')).toBe('kubernetes')
    expect(detectWorkspaceFileKind('infra/terraform/main.tf')).toBe('iac')
  })
})
