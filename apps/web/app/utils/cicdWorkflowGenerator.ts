import type {
  CicdPlatform,
  CicdSecurityConfig,
  ContainerScanToolId,
  SastLanguage,
  SastToolId,
  ScanFindingAction,
  ScanSeverityThreshold,
} from '~/types/provisioning'
import {
  defaultContainerScanToolForPlatform,
  defaultSastToolForPlatform,
  getContainerScanToolOption,
  getSastToolOption,
  normalizeContainerScanToolId,
  normalizeSastToolId,
} from '~/utils/cicdSecurityTools'

export type { CicdSecurityConfig, SastLanguage, ScanFindingAction, ScanSeverityThreshold }

/** Supply-chain pinned action refs (commit SHA + human version comment). */
export const PINNED_ACTIONS = {
  checkout: 'actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683', // v4.2.2
  setupBuildx: 'docker/setup-buildx-action@6524bf65af31da8d45b59e8c27de4bd072b392f5', // v3.8.0
  loginAction: 'docker/login-action@9780b0c442fbb1117ed29e0efdff1e18412f7567', // v3.3.0
  buildPush: 'docker/build-push-action@ca877d9245402d1537745e0e356eab47c3520991', // v6.13.0
  trivy: 'aquasecurity/trivy-action@6c175e9c4083a92bbca2f9724c8a5e33bc2d97a5', // v0.30.0
  codeqlInit: 'github/codeql-action/init@b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d', // v3.28.10
  codeqlAnalyze: 'github/codeql-action/analyze@b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d', // v3.28.10
  codeqlUploadSarif: 'github/codeql-action/upload-sarif@b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d', // v3.28.10
} as const

export interface CicdWorkflowOptions {
  branch?: string
  runner?: string
  deploymentName?: string
  namespace?: string
  containerName?: string
  /** Full image reference template (GitHub Actions / GitLab CI expressions allowed). */
  imageRef?: string
  /** Path to Dockerfile relative to repo root (defaults to Docker auto-detect). */
  dockerfilePath?: string
}

export function defaultCicdSecurityConfig(platform: CicdPlatform = 'github'): CicdSecurityConfig {
  return {
    containerScan: {
      enabled: false,
      severityThreshold: 'critical_high',
      onFinding: 'block',
      tool: defaultContainerScanToolForPlatform(platform),
    },
    sastGuardrails: {
      enabled: false,
      enableSast: true,
      enableHealthRollback: true,
      sastLanguages: ['javascript-typescript'],
      sastTool: defaultSastToolForPlatform(platform),
    },
  }
}

export function severityToTrivy(threshold: ScanSeverityThreshold): string {
  return threshold === 'critical' ? 'CRITICAL' : 'CRITICAL,HIGH'
}

export function findingActionToExitCode(action: ScanFindingAction): '0' | '1' {
  return action === 'block' ? '1' : '0'
}

function yamlList(values: string[], indent: number): string[] {
  const pad = ' '.repeat(indent)
  return values.map((value) => `${pad}- ${value}`)
}

function needsClause(deps: string[]): string {
  if (deps.length === 1) return `    needs: ${deps[0]}`
  return ['    needs:', ...deps.map((d) => `      - ${d}`)].join('\n')
}

function buildGithubSastSteps(
  security: CicdSecurityConfig,
  languages: SastLanguage[],
): string[] {
  const tool = getSastToolOption(security.sastGuardrails.sastTool)
  if (tool.codeqlInit && tool.codeqlAnalyze) {
    return [
      '      - name: Initialize CodeQL',
      `        uses: ${tool.codeqlInit}`,
      '        with:',
      '          languages: |',
      ...yamlList(languages, 12),
      '      - name: Perform CodeQL Analysis',
      `        uses: ${tool.codeqlAnalyze}`,
    ]
  }
  const image = tool.image ?? 'returntocorp/semgrep:1.97.0'
  return [
    '      - name: Run Semgrep (pinned image)',
    '        run: |',
    `          docker run --rm -v "\${GITHUB_WORKSPACE}:/src" -w /src ${image} \\`,
    '            semgrep scan --config p/ci --error --sarif --output semgrep.sarif || true',
    `          docker run --rm -v "\${GITHUB_WORKSPACE}:/src" -w /src ${image} \\`,
    '            semgrep scan --config p/security-audit --error',
  ]
}

function buildGithubTrivySteps(
  security: CicdSecurityConfig,
  severity: string,
  exitCode: string,
): string[] {
  const tool = getContainerScanToolOption(security.containerScan.tool)
  if (tool.githubAction) {
    return [
      '      - name: Run Trivy vulnerability scanner',
      `        uses: ${tool.githubAction}`,
      '        with:',
      '          image-ref: ${{ needs.build-image.outputs.image }}',
      '          format: sarif',
      '          output: trivy-results.sarif',
      `          exit-code: "${exitCode}"`,
      `          severity: ${severity}`,
      '          ignore-unfixed: true',
    ]
  }
  const image = tool.image ?? 'aquasec/trivy:0.58.1'
  const exitFlag = exitCode === '1' ? '--exit-code 1' : '--exit-code 0'
  return [
    '      - name: Run Trivy vulnerability scanner (pinned image)',
    '        run: |',
    `          docker run --rm -v /var/run/docker.sock:/var/run/docker.sock ${image} image \\`,
    `            --format sarif --output trivy-results.sarif --severity ${severity} ${exitFlag} \\`,
    '            "${{ needs.build-image.outputs.image }}"',
  ]
}

function buildGithubWorkflow(
  security: CicdSecurityConfig,
  options: Required<
    Pick<
      CicdWorkflowOptions,
      'branch' | 'runner' | 'deploymentName' | 'namespace' | 'containerName' | 'imageRef'
    >
  > & { dockerfilePath?: string },
): string {
  const solutionA = security.containerScan.enabled
  const solutionB = security.sastGuardrails.enabled
  const runSast = solutionB && security.sastGuardrails.enableSast
  const runHealth = solutionB && security.sastGuardrails.enableHealthRollback
  const severity = severityToTrivy(security.containerScan.severityThreshold)
  const exitCode = findingActionToExitCode(security.containerScan.onFinding)
  const languages = security.sastGuardrails.sastLanguages.length
    ? security.sastGuardrails.sastLanguages
    : (['javascript-typescript'] as SastLanguage[])

  const lines: string[] = [
    '# Generated by Launchpad - container scan (A) / SAST + health rollback (B)',
    `# launchpad-cicd-security: ${JSON.stringify({
      containerScan: security.containerScan,
      sastGuardrails: {
        enabled: security.sastGuardrails.enabled,
        enableSast: security.sastGuardrails.enableSast,
        enableHealthRollback: security.sastGuardrails.enableHealthRollback,
        sastLanguages: languages,
        sastTool: security.sastGuardrails.sastTool,
      },
    })}`,
    'name: Build, Scan & Deploy',
    'on:',
    '  push:',
    `    branches: ["${options.branch}"]`,
    '  workflow_dispatch:',
    '',
    'env:',
    `  IMAGE_REF: ${options.imageRef}`,
    `  DEPLOYMENT_NAME: ${options.deploymentName}`,
    `  CONTAINER_NAME: ${options.containerName}`,
    `  K8S_NAMESPACE: ${options.namespace}`,
    '',
    'permissions:',
    '  contents: read',
    '  packages: write',
    '  security-events: write',
    '  actions: read',
    '',
    'jobs:',
  ]

  if (runSast) {
    lines.push(
      '  sast-code-scan:',
      '    name: Code Security Analysis (Solution B)',
      `    runs-on: ${options.runner}`,
      '    steps:',
      `      - name: Checkout`,
      `        uses: ${PINNED_ACTIONS.checkout}`,
      ...buildGithubSastSteps(security, languages),
      '',
    )
  }

  const buildNeeds = runSast ? ['sast-code-scan'] : []
  lines.push(
    '  build-image:',
    '    name: Build & Push Image',
    ...(buildNeeds.length ? [needsClause(buildNeeds)] : []),
    `    runs-on: ${options.runner}`,
    '    outputs:',
    '      image: ${{ steps.meta.outputs.image }}',
    '    steps:',
    `      - name: Checkout`,
    `        uses: ${PINNED_ACTIONS.checkout}`,
    '      - name: Set image metadata',
    '        id: meta',
    '        run: echo "image=${IMAGE_REF}" >> "$GITHUB_OUTPUT"',
    '      - name: Set up Docker Buildx',
    `        uses: ${PINNED_ACTIONS.setupBuildx}`,
    '      - name: Log in to GHCR',
    `        uses: ${PINNED_ACTIONS.loginAction}`,
    '        with:',
    '          registry: ghcr.io',
    '          username: ${{ github.actor }}',
    '          password: ${{ secrets.GITHUB_TOKEN }}',
    '      - name: Build and push',
    `        uses: ${PINNED_ACTIONS.buildPush}`,
    '        with:',
    '          context: .',
    ...(options.dockerfilePath
      ? [`          file: ${options.dockerfilePath}`]
      : []),
    '          push: true',
    '          tags: ${{ env.IMAGE_REF }}',
    '',
  )

  if (solutionA) {
    lines.push(
      '  container-security-scan:',
      '    name: Container Vulnerability Scan (Solution A)',
      '    needs: build-image',
      `    runs-on: ${options.runner}`,
      '    steps:',
      ...buildGithubTrivySteps(security, severity, exitCode),
      '      - name: Upload Trivy SARIF',
      `        uses: ${PINNED_ACTIONS.codeqlUploadSarif}`,
      '        if: always()',
      '        with:',
      '          sarif_file: trivy-results.sarif',
      '          category: trivy-container-scan',
      '',
    )
  }

  const deployNeeds = solutionA ? ['container-security-scan'] : ['build-image']
  const deployScript = runHealth
    ? [
        '          set -euo pipefail',
        '          kubectl set image "deployment/${DEPLOYMENT_NAME}" \\',
        '            "${CONTAINER_NAME}=${IMAGE_REF}" \\',
        '            -n "${K8S_NAMESPACE}"',
        '          if ! kubectl rollout status "deployment/${DEPLOYMENT_NAME}" \\',
        '            -n "${K8S_NAMESPACE}" --timeout=120s; then',
        '            echo "Deployment failed health check - initiating automated rollback..."',
        '            kubectl rollout undo "deployment/${DEPLOYMENT_NAME}" -n "${K8S_NAMESPACE}"',
        '            exit 1',
        '          fi',
      ]
    : [
        '          set -euo pipefail',
        '          kubectl set image "deployment/${DEPLOYMENT_NAME}" \\',
        '            "${CONTAINER_NAME}=${IMAGE_REF}" \\',
        '            -n "${K8S_NAMESPACE}"',
        '          kubectl rollout status "deployment/${DEPLOYMENT_NAME}" \\',
        '            -n "${K8S_NAMESPACE}" --timeout=120s || true',
      ]

  lines.push(
    '  deploy:',
    '    name: Deploy to Kubernetes',
    needsClause(deployNeeds),
    `    runs-on: ${options.runner}`,
    '    environment: production',
    '    steps:',
    `      - name: Checkout`,
    `        uses: ${PINNED_ACTIONS.checkout}`,
    '      - name: Deploy & verify health',
    '        env:',
    '          KUBECONFIG: ${{ secrets.KUBECONFIG }}',
    '        run: |',
    ...deployScript,
    '',
  )

  return lines.join('\n')
}

function buildGitlabPipeline(
  security: CicdSecurityConfig,
  options: Required<
    Pick<
      CicdWorkflowOptions,
      'branch' | 'runner' | 'deploymentName' | 'namespace' | 'containerName' | 'imageRef'
    >
  > & { dockerfilePath?: string },
): string {
  const solutionA = security.containerScan.enabled
  const solutionB = security.sastGuardrails.enabled
  const runSast = solutionB && security.sastGuardrails.enableSast
  const runHealth = solutionB && security.sastGuardrails.enableHealthRollback
  const severity = severityToTrivy(security.containerScan.severityThreshold)
  const failOnVuln = security.containerScan.onFinding === 'block'
  const languages = security.sastGuardrails.sastLanguages.length
    ? security.sastGuardrails.sastLanguages
    : (['javascript-typescript'] as SastLanguage[])

  const stages: string[] = []
  if (runSast) stages.push('sast')
  stages.push('build')
  if (solutionA) stages.push('scan')
  stages.push('deploy')

  const lines: string[] = [
    '# Generated by Launchpad - container scan (A) / SAST + health rollback (B)',
    `# launchpad-cicd-security: ${JSON.stringify({
      containerScan: security.containerScan,
      sastGuardrails: {
        enabled: security.sastGuardrails.enabled,
        enableSast: security.sastGuardrails.enableSast,
        enableHealthRollback: security.sastGuardrails.enableHealthRollback,
        sastLanguages: languages,
        sastTool: security.sastGuardrails.sastTool,
      },
    })}`,
    `stages: [${stages.join(', ')}]`,
    '',
    'variables:',
    `  IMAGE_REF: ${options.imageRef}`,
    `  DEPLOYMENT_NAME: ${options.deploymentName}`,
    `  CONTAINER_NAME: ${options.containerName}`,
    `  K8S_NAMESPACE: ${options.namespace}`,
    '',
  ]

  if (runSast) {
    const sastImage = getSastToolOption(security.sastGuardrails.sastTool).image ?? 'returntocorp/semgrep:1.97.0'
    lines.push(
      'sast-code-scan:',
      '  stage: sast',
      `  image: ${sastImage}`,
      '  script:',
      '    - semgrep scan --config p/ci --error --sarif --output semgrep.sarif || true',
      '    - semgrep scan --config p/security-audit --error',
      '  artifacts:',
      '    when: always',
      '    paths:',
      '      - semgrep.sarif',
      '    reports:',
      '      sast: semgrep.sarif',
      '  rules:',
      `    - if: $CI_COMMIT_BRANCH == "${options.branch}"`,
      '',
    )
  }

  const dockerFileFlag = options.dockerfilePath ? `-f "${options.dockerfilePath}" ` : ''
  lines.push(
    'build-image:',
    '  stage: build',
    ...(runSast ? ['  needs: ["sast-code-scan"]'] : []),
    '  image: docker:27',
    '  services:',
    '    - docker:27-dind',
    '  script:',
    `    - docker build ${dockerFileFlag}-t "$IMAGE_REF" .`,
    '    - docker push "$IMAGE_REF"',
    '  rules:',
    `    - if: $CI_COMMIT_BRANCH == "${options.branch}"`,
    '',
  )

  if (solutionA) {
    const exitFlag = failOnVuln ? '--exit-code 1' : '--exit-code 0'
    const trivyImage = getContainerScanToolOption(security.containerScan.tool).image ?? 'aquasec/trivy:0.58.1'
    lines.push(
      'container-security-scan:',
      '  stage: scan',
      '  needs: ["build-image"]',
      '  image:',
      `    name: ${trivyImage}`,
      '    entrypoint: [""]',
      '  script:',
      `    - trivy image --format sarif --output trivy-results.sarif --severity ${severity} ${exitFlag} "$IMAGE_REF"`,
      '  artifacts:',
      '    when: always',
      '    paths:',
      '      - trivy-results.sarif',
      '    reports:',
      '      container_scanning: trivy-results.sarif',
      '  rules:',
      `    - if: $CI_COMMIT_BRANCH == "${options.branch}"`,
      '',
    )
  }

  const deployNeeds = solutionA ? '["container-security-scan"]' : '["build-image"]'
  const healthBlock = runHealth
    ? [
        '    - |',
        '      set -euo pipefail',
        '      kubectl set image "deployment/${DEPLOYMENT_NAME}" \\',
        '        "${CONTAINER_NAME}=${IMAGE_REF}" -n "${K8S_NAMESPACE}"',
        '      if ! kubectl rollout status "deployment/${DEPLOYMENT_NAME}" \\',
        '        -n "${K8S_NAMESPACE}" --timeout=120s; then',
        '        echo "Deployment failed health check - initiating automated rollback..."',
        '        kubectl rollout undo "deployment/${DEPLOYMENT_NAME}" -n "${K8S_NAMESPACE}"',
        '        exit 1',
        '      fi',
      ]
    : [
        '    - kubectl set image "deployment/${DEPLOYMENT_NAME}" "${CONTAINER_NAME}=${IMAGE_REF}" -n "${K8S_NAMESPACE}"',
        '    - kubectl rollout status "deployment/${DEPLOYMENT_NAME}" -n "${K8S_NAMESPACE}" --timeout=120s || true',
      ]

  lines.push(
    'deploy:',
    '  stage: deploy',
    `  needs: ${deployNeeds}`,
    `  image: ${options.runner.includes('/') ? options.runner : 'bitnami/kubectl:1.31'}`,
    '  script:',
    ...healthBlock,
    '  rules:',
    `    - if: $CI_COMMIT_BRANCH == "${options.branch}"`,
    '',
  )

  return lines.join('\n')
}

function inferPlatformFromWorkflow(content: string): CicdPlatform {
  if (/runs-on:|github\.|GITHUB_WORKSPACE/.test(content)) return 'github'
  return 'gitlab'
}

function inferSastToolFromContent(content: string, platform: CicdPlatform): SastToolId {
  if (/codeql-action\/init/.test(content)) return 'codeql-v3.28.10'
  const semgrep = content.match(/returntocorp\/semgrep:([0-9.]+)/)
  if (semgrep?.[1]) {
    const id = `semgrep-${semgrep[1]}` as SastToolId
    if (normalizeSastToolId(id, platform) === id) return id
  }
  return defaultSastToolForPlatform(platform)
}

function inferContainerScanToolFromContent(content: string, platform: CicdPlatform): ContainerScanToolId {
  if (/aquasecurity\/trivy-action@/.test(content)) return 'trivy-action-v0.30.0'
  const trivy = content.match(/aquasec\/trivy:([0-9.]+)/)
  if (trivy?.[1]) {
    const id = `trivy-${trivy[1]}` as ContainerScanToolId
    if (normalizeContainerScanToolId(id, platform) === id) return id
  }
  return defaultContainerScanToolForPlatform(platform)
}

export function renderCicdWorkflow(
  platform: CicdPlatform,
  security: CicdSecurityConfig = defaultCicdSecurityConfig(platform),
  options: CicdWorkflowOptions = {},
): string {
  const resolved = {
    branch: options.branch ?? 'main',
    runner: options.runner ?? (platform === 'github' ? 'ubuntu-latest' : 'docker:27'),
    deploymentName: options.deploymentName ?? 'app',
    namespace: options.namespace ?? 'lp-app',
    containerName: options.containerName ?? 'app',
    imageRef:
      options.imageRef
      ?? (platform === 'github'
        ? 'ghcr.io/${{ github.repository }}:${{ github.sha }}'
        : '${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHA}'),
    dockerfilePath: options.dockerfilePath,
  }
  return platform === 'github'
    ? buildGithubWorkflow(security, resolved)
    : buildGitlabPipeline(security, resolved)
}

export function parseCicdSecurityMarker(content: string, platform?: CicdPlatform): CicdSecurityConfig | null {
  const match = content.match(/# launchpad-cicd-security:\s*(\{.*\})\s*$/m)
  if (!match?.[1]) return null
  const resolvedPlatform = platform ?? inferPlatformFromWorkflow(content)
  try {
    const parsed = JSON.parse(match[1]) as Partial<CicdSecurityConfig>
    const defaults = defaultCicdSecurityConfig(resolvedPlatform)
    return {
      containerScan: {
        ...defaults.containerScan,
        ...(parsed.containerScan ?? {}),
        tool: normalizeContainerScanToolId(parsed.containerScan?.tool, resolvedPlatform),
      },
      sastGuardrails: {
        ...defaults.sastGuardrails,
        ...(parsed.sastGuardrails ?? {}),
        sastTool: normalizeSastToolId(parsed.sastGuardrails?.sastTool, resolvedPlatform),
      },
    }
  } catch {
    return null
  }
}

export function inferCicdSecurityFromContent(content: string): CicdSecurityConfig {
  const fromMarker = parseCicdSecurityMarker(content)
  if (fromMarker) return fromMarker

  const platform = inferPlatformFromWorkflow(content)
  const defaults = defaultCicdSecurityConfig(platform)
  const hasContainerScan = /container-security-scan:/.test(content) || /trivy image/.test(content)
  const hasSast = /sast-code-scan:/.test(content) || /codeql-action\/init/.test(content) || /semgrep scan/.test(content)
  const hasHealth = /rollout undo/.test(content)

  const severity: ScanSeverityThreshold = /severity:\s*CRITICAL\b(?!,HIGH)/.test(content)
    ? 'critical'
    : 'critical_high'
  const onFinding: ScanFindingAction =
    /exit-code:\s*["']?0["']?/.test(content) || /--exit-code 0/.test(content) ? 'warn' : 'block'

  return {
    containerScan: {
      enabled: hasContainerScan,
      severityThreshold: severity,
      onFinding,
      tool: hasContainerScan
        ? inferContainerScanToolFromContent(content, platform)
        : defaults.containerScan.tool,
    },
    sastGuardrails: {
      enabled: hasSast || hasHealth,
      enableSast: hasSast,
      enableHealthRollback: hasHealth,
      sastLanguages: defaults.sastGuardrails.sastLanguages,
      sastTool: hasSast
        ? inferSastToolFromContent(content, platform)
        : defaults.sastGuardrails.sastTool,
    },
  }
}
