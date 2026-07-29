import { describe, expect, it } from 'vitest'
import { planDiagnosticPatchApply } from '~/utils/diagnosticPatchApply'

const patch = {
  targetFile: 'infra/k8s/manifests/deployment.yaml',
  originalContent: 'runAsNonRoot: true',
  suggestedContent: 'runAsNonRoot: true\n        runAsUser: 101',
}

describe('planDiagnosticPatchApply', () => {
  it('replaces original snippet when present', () => {
    const current = 'spec:\n      securityContext:\n        runAsNonRoot: true\n'
    const plan = planDiagnosticPatchApply(current, patch)
    expect(plan.mode).toBe('replaced')
    expect(plan.nextContent).toContain('runAsUser: 101')
    expect(plan.nextContent).not.toBe(current)
  })

  it('creates file when content is missing', () => {
    const plan = planDiagnosticPatchApply(null, patch)
    expect(plan.mode).toBe('created')
    expect(plan.nextContent).toBe(patch.suggestedContent)
  })

  it('appends when original snippet is not found', () => {
    const current = 'kind: Deployment\n'
    const plan = planDiagnosticPatchApply(current, patch)
    expect(plan.mode).toBe('appended')
    expect(plan.nextContent).toContain('# launchpad-analyzer-fix:')
    expect(plan.nextContent).toContain(patch.suggestedContent)
  })
})
