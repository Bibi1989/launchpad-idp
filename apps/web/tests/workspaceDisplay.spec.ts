import { describe, expect, it } from 'vitest'
import {
  artifactModeLabel,
  workspaceStackLabel,
  workspaceStackParts,
} from '../app/utils/workspaceDisplay'

describe('workspaceStackLabel', () => {
  it('hides terraform for manifests-only workspaces', () => {
    expect(
      workspaceStackLabel({
        engine: 'terraform',
        provider: 'local',
        artifact_mode: 'manifest_only',
        status: 'ready',
        files: ['infra/k8s/manifests/deployment.yaml', 'infra/k8s/manifests/service.yaml'],
      }),
    ).toBe('k8s · local · ready')
  })

  it('shows engine + k8s for both mode', () => {
    expect(
      workspaceStackLabel({
        engine: 'terraform',
        provider: 'gcp',
        artifact_mode: 'both',
        status: 'ready',
      }),
    ).toBe('terraform + k8s · gcp · ready')
  })

  it('infers k8s from files when mode is missing terraform paths', () => {
    expect(
      workspaceStackLabel({
        engine: 'terraform',
        provider: 'local',
        artifact_mode: null,
        files: ['infra/k8s/manifests/namespace.yaml'],
      }),
    ).toBe('k8s · local')
  })
})

describe('workspaceStackParts', () => {
  it('splits status for header status dot', () => {
    expect(
      workspaceStackParts({
        engine: 'terraform',
        provider: 'local',
        artifact_mode: 'manifest_only',
        status: 'ready',
        files: ['infra/k8s/manifests/deployment.yaml'],
      }),
    ).toEqual({ stack: 'k8s', provider: 'local', status: 'ready' })
  })
})

describe('artifactModeLabel', () => {
  it('labels modes', () => {
    expect(artifactModeLabel('manifest_only')).toBe('manifests only')
    expect(artifactModeLabel('both')).toBe('iac + manifests')
    expect(artifactModeLabel('iac_only')).toBe('iac only')
  })
})
