import { describe, expect, it } from 'vitest'
import {
  defaultCicdSecurityConfig,
  inferCicdSecurityFromContent,
  PINNED_ACTIONS,
  renderCicdWorkflow,
} from '../app/utils/cicdWorkflowGenerator'
import {
  artifactModeToInfraConfig,
  buildCiCdScaffold,
  buildKubernetesScaffold,
  buildProvisionScaffold,
  defaultInfraGenerationConfig,
  infraConfigToArtifactMode,
  infraConfigToKubernetesPackaging,
  kubernetesRunCommands,
  provisionRunCommands,
} from '../app/utils/workspaceInfraScaffold'

function securityWith(opts: {
  a?: boolean
  b?: boolean
  sast?: boolean
  health?: boolean
  severity?: 'critical' | 'critical_high'
  onFinding?: 'block' | 'warn'
}) {
  const base = defaultCicdSecurityConfig()
  return {
    containerScan: {
      ...base.containerScan,
      enabled: Boolean(opts.a),
      severityThreshold: opts.severity ?? 'critical_high',
      onFinding: opts.onFinding ?? 'block',
    },
    sastGuardrails: {
      ...base.sastGuardrails,
      enabled: Boolean(opts.b),
      enableSast: opts.sast ?? true,
      enableHealthRollback: opts.health ?? true,
    },
  }
}

describe('workspaceInfraScaffold', () => {
  it('maps infra generation config to artifact mode', () => {
    expect(
      infraConfigToArtifactMode({
        provision: { enabled: true, engine: 'terraform' },
        kubernetes: { enabled: false, mode: 'k8s' },
        cicd: { enabled: false, platform: 'github', security: defaultCicdSecurityConfig() },
      }),
    ).toBe('iac_only')

    expect(
      infraConfigToArtifactMode({
        provision: { enabled: false, engine: 'terraform' },
        kubernetes: { enabled: true, mode: 'helm' },
        cicd: { enabled: true, platform: 'gitlab', security: defaultCicdSecurityConfig() },
      }),
    ).toBe('manifest_only')

    expect(
      infraConfigToArtifactMode({
        provision: { enabled: true, engine: 'pulumi' },
        kubernetes: { enabled: true, mode: 'k8s' },
        cicd: { enabled: false, platform: 'github', security: defaultCicdSecurityConfig() },
      }),
    ).toBe('both')
  })

  it('maps infra generation config to kubernetes packaging', () => {
    expect(
      infraConfigToKubernetesPackaging({
        provision: { enabled: true, engine: 'terraform' },
        kubernetes: { enabled: false, mode: 'k8s' },
        cicd: { enabled: false, platform: 'github', security: defaultCicdSecurityConfig() },
      }),
    ).toBe('none')

    expect(
      infraConfigToKubernetesPackaging({
        provision: { enabled: true, engine: 'terraform' },
        kubernetes: { enabled: true, mode: 'helm' },
        cicd: { enabled: false, platform: 'github', security: defaultCicdSecurityConfig() },
      }),
    ).toBe('helm')
  })

  it('round-trips artifact mode to infra config', () => {
    const config = artifactModeToInfraConfig('both', 'pulumi', 'helm')
    expect(config.provision).toEqual({ enabled: true, engine: 'pulumi' })
    expect(config.kubernetes).toEqual({ enabled: true, mode: 'helm' })
    expect(config.cicd.enabled).toBe(false)
    expect(config.cicd.security.containerScan.enabled).toBe(false)
  })

  it('builds local defaults without provision', () => {
    const config = defaultInfraGenerationConfig({ isLocal: true })
    expect(config.provision.enabled).toBe(false)
    expect(config.kubernetes.enabled).toBe(true)
  })

  it('writes scaffold files under infra/', () => {
    const provision = buildProvisionScaffold('ws-1', 'demo', 'terraform')
    expect(provision[0]?.path).toBe('infra/terraform/main.tf')

    const k8s = buildKubernetesScaffold('k8s')
    expect(k8s.every((item) => item.path.startsWith('infra/k8s/'))).toBe(true)

    const cicd = buildCiCdScaffold('github')
    expect(cicd[0]?.path).toBe('ci/github/workflows/deploy.yml')
    expect(cicd[0]?.content).toContain('build-image:')
    expect(cicd[0]?.content).toContain('deploy:')
  })

  it('returns separate run commands per area', () => {
    expect(provisionRunCommands('terraform')).toHaveLength(2)
    expect(kubernetesRunCommands('helm')).toEqual([
      'helm upgrade --install app-chart infra/helm/app-chart/',
    ])
  })
})

describe('cicdWorkflowGenerator', () => {
  it('pins actions to commit SHAs', () => {
    const yaml = renderCicdWorkflow('github', securityWith({ a: true, b: true }))
    expect(yaml).toContain(PINNED_ACTIONS.checkout)
    expect(yaml).toContain(PINNED_ACTIONS.trivy)
    expect(yaml).toContain(PINNED_ACTIONS.codeqlInit)
    expect(yaml).not.toMatch(/uses:\s*actions\/checkout@v\d/)
    expect(yaml).not.toMatch(/uses:\s*aquasecurity\/trivy-action@0\./)
  })

  it('generates neither A nor B as build → deploy', () => {
    const yaml = renderCicdWorkflow('github', securityWith({}))
    expect(yaml).toContain('build-image:')
    expect(yaml).toContain('deploy:')
    expect(yaml).not.toContain('sast-code-scan:')
    expect(yaml).not.toContain('container-security-scan:')
    expect(yaml).not.toContain('rollout undo')
  })

  it('generates Solution A only between build and deploy', () => {
    const yaml = renderCicdWorkflow(
      'github',
      securityWith({ a: true, severity: 'critical', onFinding: 'warn' }),
    )
    expect(yaml).toContain('container-security-scan:')
    expect(yaml).toContain('severity: CRITICAL')
    expect(yaml).toContain('exit-code: "0"')
    expect(yaml).toContain('needs: container-security-scan')
    expect(yaml).not.toContain('sast-code-scan:')
  })

  it('generates Solution B SAST before build and health rollback in deploy', () => {
    const yaml = renderCicdWorkflow('github', securityWith({ b: true, sast: true, health: true }))
    expect(yaml).toContain('sast-code-scan:')
    expect(yaml).toContain('needs: sast-code-scan')
    expect(yaml).toContain('rollout undo')
    expect(yaml).not.toContain('container-security-scan:')
  })

  it('orders BOTH as SAST → build → scan → deploy', () => {
    const yaml = renderCicdWorkflow('github', securityWith({ a: true, b: true }))
    const sast = yaml.indexOf('sast-code-scan:')
    const build = yaml.indexOf('build-image:')
    const scan = yaml.indexOf('container-security-scan:')
    const deploy = yaml.indexOf('\n  deploy:')
    expect(sast).toBeGreaterThan(-1)
    expect(build).toBeGreaterThan(sast)
    expect(scan).toBeGreaterThan(build)
    expect(deploy).toBeGreaterThan(scan)
    expect(yaml).toContain('needs: container-security-scan')
    expect(yaml).toContain('rollout undo')
  })

  it('generates GitLab equivalent stages for BOTH', () => {
    const yaml = renderCicdWorkflow('gitlab', securityWith({ a: true, b: true }))
    expect(yaml).toContain('stages: [sast, build, scan, deploy]')
    expect(yaml).toContain('sast-code-scan:')
    expect(yaml).toContain('container-security-scan:')
    expect(yaml).toContain('trivy image')
    expect(yaml).toContain('rollout undo')
  })

  it('round-trips security marker', () => {
    const security = securityWith({ a: true, b: true, severity: 'critical', onFinding: 'warn' })
    const yaml = renderCicdWorkflow('github', security)
    const inferred = inferCicdSecurityFromContent(yaml)
    expect(inferred.containerScan.enabled).toBe(true)
    expect(inferred.containerScan.severityThreshold).toBe('critical')
    expect(inferred.containerScan.onFinding).toBe('warn')
    expect(inferred.sastGuardrails.enabled).toBe(true)
    expect(inferred.sastGuardrails.enableSast).toBe(true)
    expect(inferred.sastGuardrails.enableHealthRollback).toBe(true)
  })

  it('scaffolds gitlab path with security config', () => {
    const files = buildCiCdScaffold('gitlab', securityWith({ a: true }))
    expect(files[0]?.path).toBe('ci/gitlab/.gitlab-ci.yml')
    expect(files[0]?.content).toContain('container-security-scan:')
  })
})
