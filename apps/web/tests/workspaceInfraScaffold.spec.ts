import { describe, expect, it } from 'vitest'
import {
  defaultCicdSecurityConfig,
  inferCicdSecurityFromContent,
  PINNED_ACTIONS,
  renderCicdWorkflow,
} from '../app/utils/cicdWorkflowGenerator'
import {
  applyDetectedWorkspaceInfra,
  artifactModeToInfraConfig,
  buildCiCdScaffold,
  buildDockerScaffold,
  buildKubernetesScaffold,
  buildProvisionScaffold,
  defaultInfraGenerationConfig,
  detectWorkspaceInfraFromPaths,
  infraConfigToArtifactMode,
  infraConfigToKubernetesPackaging,
  matchFrameworkSlug,
  kubernetesRunCommands,
  provisionRunCommands,
  iacDestroyCommand,
  iacDestroyWizardSteps,
  iacInitWizardSteps,
  iacRunShortcuts,
  iacToolbarActions,
} from '../app/utils/workspaceInfraScaffold'
import { buildRepoScaffoldBundle } from '../app/utils/workspaceRepoScaffold'

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

  it('writes one CI workflow per selected framework', () => {
    const files = buildCiCdScaffold('github', undefined, ['nuxtjs', 'fastapi', 'nestjs'], 'demo')
    expect(files.map((f) => f.path)).toEqual([
      'ci/github/workflows/nuxtjs-deploy.yml',
      'ci/github/workflows/fastapi-deploy.yml',
      'ci/github/workflows/nestjs-deploy.yml',
    ])
    expect(files[0]?.content).toContain('file: dockers/Dockerfile.demo-nuxtjs')
    expect(files[1]?.content).toContain('file: dockers/Dockerfile.demo-fastapi')
    expect(files[2]?.content).toContain('file: dockers/Dockerfile.demo-nestjs')
  })

  it('writes gitlab include root plus per-framework fragments', () => {
    const files = buildCiCdScaffold('gitlab', undefined, ['nuxtjs', 'fastapi'], 'demo')
    expect(files[0]?.path).toBe('ci/gitlab/.gitlab-ci.yml')
    expect(files[0]?.content).toContain('local: ci/gitlab/nuxtjs.yml')
    expect(files.map((f) => f.path)).toContain('ci/gitlab/fastapi.yml')
    expect(files.find((f) => f.path === 'ci/gitlab/fastapi.yml')?.content).toContain(
      '-f "dockers/Dockerfile.demo-fastapi"',
    )
  })

  it('writes dockers/ and docker-compose.yml when container scaffold is enabled', () => {
    expect(buildDockerScaffold({ enabled: false } as never)).toEqual([])

    const single = buildDockerScaffold({
      enabled: true,
      generate_dockerfile: true,
      generate_docker_compose: true,
      stack: 'fastapi',
      frameworks: [],
      app_name: 'demo',
      listen_port: 8000,
    })
    expect(single.map((f) => f.path)).toEqual(['dockers/Dockerfile.demo', 'docker-compose.yml'])
    expect(single[0]?.content).toContain('uvicorn')

    const multi = buildDockerScaffold({
      enabled: true,
      generate_dockerfile: true,
      generate_docker_compose: true,
      stack: 'nuxtjs',
      frameworks: ['nuxtjs', 'fastapi', 'nestjs'],
      app_name: 'demo',
      listen_port: 8080,
    })
    expect(multi.map((f) => f.path)).toEqual([
      'dockers/Dockerfile.demo-nuxtjs',
      'dockers/Dockerfile.demo-fastapi',
      'dockers/Dockerfile.demo-nestjs',
      'docker-compose.yml',
    ])
    expect(multi[3]?.content).toContain('dockerfile: dockers/Dockerfile.demo-fastapi')
  })

  it('skips client docker scaffold when multi-service specs are present', () => {
    expect(
      buildDockerScaffold({
        enabled: true,
        generate_dockerfile: true,
        generate_docker_compose: true,
        stack: 'nextjs',
        frameworks: [],
        app_name: 'web-ui',
        listen_port: 3000,
        services: [
          { name: 'web-ui', app_kind: 'frontend', stack: 'nextjs', listen_port: 3000 },
          { name: 'api-server', app_kind: 'backend', stack: 'node', listen_port: 8080 },
        ],
      } as never),
    ).toEqual([])
  })

  it('returns separate run commands per area', () => {
    expect(provisionRunCommands('terraform')).toHaveLength(2)
    expect(provisionRunCommands('terraform')[0]).toContain('terraform init')
    expect(provisionRunCommands('terraform')[0]).toContain('infra/terraform')
    expect(provisionRunCommands('terraform')[0]).toContain('ls ./*.tf')
    expect(iacRunShortcuts('pulumi').map((s) => s.label)).toEqual([
      'npm install',
      'preview',
      'up',
      'refresh',
      'down',
    ])
    expect(iacRunShortcuts('terraform').some((s) => s.id === 'tf-destroy' && s.danger)).toBe(true)
    expect(iacRunShortcuts('terraform').find((s) => s.id === 'tf-init')?.opensInitWizard).toBe(true)
    expect(iacDestroyCommand('terraform')).toContain('destroy -auto-approve')
    expect(iacInitWizardSteps('terraform').map((s) => s.label)).toEqual([
      'init',
      'validate',
      'plan',
      'apply',
    ])
    expect(iacInitWizardSteps('terraform', { enableGcpApis: true }).map((s) => s.label)).toEqual([
      'enable APIs',
      'init',
      'validate',
      'plan',
      'apply',
    ])
    expect(iacInitWizardSteps('terraform').find((s) => s.id === 'terraform-apply')?.command).toContain(
      '-auto-approve',
    )
    expect(iacDestroyWizardSteps('terraform')).toHaveLength(1)
    expect(iacToolbarActions('terraform').provision.label).toBe('Provision stack')
    expect(iacToolbarActions('ansible').provision.label).toBe('Run Ansible')
    expect(kubernetesRunCommands('helm')).toEqual([
      'helm upgrade --install app-chart infra/helm/app-chart/',
    ])
  })

  it('detects Ansible under infra/ansible alongside Terraform', () => {
    const detected = detectWorkspaceInfraFromPaths([
      'infra/ansible/playbooks/site.yml',
      'infra/ansible/inventory/hosts.yml',
      'infra/terraform/main.tf',
    ])
    expect(detected.ansible.enabled).toBe(true)
    expect(detected.provision.enabled).toBe(true)
    expect(detected.provision.engine).toBe('terraform')
    expect(detected.summary.some((s) => s.includes('Ansible'))).toBe(true)
  })

  it('detects ansible-only workspaces', () => {
    const detected = detectWorkspaceInfraFromPaths([
      'infra/ansible/ansible.cfg',
      'infra/ansible/playbooks/site.yml',
    ])
    expect(detected.ansible.enabled).toBe(true)
    expect(detected.provision.engine).toBe('ansible')
  })

  it('builds multi-artifact repo scaffold bundle', () => {
    const files = buildRepoScaffoldBundle({
      appName: 'demo',
      infra: {
        provision: { enabled: true, engine: 'terraform' },
        kubernetes: { enabled: true, mode: 'kustomize' },
        cicd: {
          enabled: true,
          platform: 'github',
          security: defaultCicdSecurityConfig(),
          frameworks: ['fastapi'],
        },
      },
      containerScaffold: {
        enabled: true,
        generate_dockerfile: true,
        generate_docker_compose: true,
        stack: 'fastapi',
        frameworks: ['fastapi'],
        app_name: 'demo',
        listen_port: 8000,
      },
      detectedFramework: 'fastapi',
    })
    const paths = files.map((f) => f.path)
    expect(paths).toContain('dockers/Dockerfile.demo-fastapi')
    expect(paths).toContain('infra/terraform/main.tf')
    expect(paths).toContain('infra/kustomize/base/kustomization.yaml')
    expect(paths.some((p) => p.startsWith('ci/github/workflows/'))).toBe(true)
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

  it('uses selected Semgrep image in GitLab pipeline', () => {
    const security = securityWith({ b: true, sast: true })
    security.sastGuardrails.sastTool = 'semgrep-1.96.0'
    const yaml = renderCicdWorkflow('gitlab', security)
    expect(yaml).toContain('returntocorp/semgrep:1.96.0')
  })

  it('uses selected Trivy image in GitLab pipeline', () => {
    const security = securityWith({ a: true })
    security.containerScan.tool = 'trivy-0.57.2'
    const yaml = renderCicdWorkflow('gitlab', security)
    expect(yaml).toContain('aquasec/trivy:0.57.2')
  })

  it('matches framework slugs from path segments', () => {
    expect(matchFrameworkSlug('nuxtjs')).toBe('nuxtjs')
    expect(matchFrameworkSlug('react-vite')).toBe('react_vite')
    expect(matchFrameworkSlug('springboot')).toBe('springboot')
    expect(matchFrameworkSlug('deploy')).toBeNull()
  })

  it('detects provision, k8s, docker and CI stacks from workspace paths', () => {
    const detected = detectWorkspaceInfraFromPaths([
      'infra/k8s/manifests/deployment.yaml',
      'dockers/Dockerfile.paygo-nuxtjs',
      'dockers/Dockerfile.paygo-fastapi',
      'docker-compose.yml',
      'ci/github/workflows/nuxtjs-deploy.yml',
      'ci/github/workflows/fastapi-deploy.yml',
      'infra/README.md',
    ])
    expect(detected.provision.enabled).toBe(false)
    expect(detected.kubernetes).toEqual({ enabled: true, mode: 'k8s' })
    expect(detected.container.enabled).toBe(true)
    expect(detected.container.generate_docker_compose).toBe(true)
    expect(detected.container.frameworks).toEqual(['nuxtjs', 'fastapi'])
    expect(detected.cicd.enabled).toBe(true)
    expect(detected.cicd.platform).toBe('github')
    expect(detected.cicd.frameworks).toEqual(['nuxtjs', 'fastapi'])
    expect(detected.summary.some((s) => s.includes('CI/CD'))).toBe(true)
  })

  it('overlays detection onto wizard defaults for the interactive form', () => {
    const base = artifactModeToInfraConfig('manifest_only', 'terraform', 'raw_manifests')
    const container = {
      enabled: false,
      generate_dockerfile: true,
      generate_docker_compose: false,
      stack: 'generic' as const,
      frameworks: [] as const,
      app_name: 'paygo',
      listen_port: 8080,
    }
    const detected = detectWorkspaceInfraFromPaths([
      'ci/gitlab/.gitlab-ci.yml',
      'ci/gitlab/nestjs.yml',
      'dockers/Dockerfile.paygo-nestjs',
    ])
    const merged = applyDetectedWorkspaceInfra(base, { ...container, frameworks: [] }, detected)
    expect(merged.infra.cicd.enabled).toBe(true)
    expect(merged.infra.cicd.platform).toBe('gitlab')
    expect(merged.infra.cicd.frameworks).toEqual(['nestjs'])
    expect(merged.container.enabled).toBe(true)
    expect(merged.container.frameworks).toEqual(['nestjs'])
    expect(merged.container.stack).toBe('nestjs')
  })
})
