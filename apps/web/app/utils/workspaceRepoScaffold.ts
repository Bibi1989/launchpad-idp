import type { FrameworkOption } from '~/types/provisioning'
import type { InfraGenerationConfig, ContainerScaffoldConfig } from '~/types/provisioning'
import type { ProjectStack } from '~/types/dockerfileSchema'
import type { WorkspaceFileAnalysisReport } from '~/utils/workspaceFileAnalysis'
import {
  buildCiCdScaffold,
  buildDockerScaffold,
  buildKubernetesScaffold,
  buildProvisionScaffold,
} from '~/utils/workspaceInfraScaffold'

export interface RepoScaffoldContext {
  installationId: number
  fullName: string
  ref?: string | null
}

export interface ScaffoldTarget {
  path: string
  content: string
}

const PROJECT_STACK_TO_FRAMEWORK: Partial<Record<ProjectStack, FrameworkOption>> = {
  node: 'node',
  python: 'python',
  go: 'go',
  java: 'java',
  rust: 'rust',
  react_vite: 'react_vite',
  nextjs: 'nextjs',
  nuxtjs: 'nuxtjs',
  vuejs: 'vuejs',
  svelte: 'svelte',
  fastapi: 'fastapi',
  flask: 'flask',
  django: 'django',
  express: 'express',
  nestjs: 'nestjs',
  springboot: 'springboot',
}

export function mapDetectedStackToFramework(stack: ProjectStack): FrameworkOption | null {
  return PROJECT_STACK_TO_FRAMEWORK[stack] ?? null
}

export async function detectRepoStackForScaffold(
  scanRepo: (input: {
    installation_id: number
    full_name: string
    ref?: string | null
  }) => Promise<{ detected_stack: ProjectStack }>,
  ctx: RepoScaffoldContext,
): Promise<FrameworkOption | null> {
  try {
    const result = await scanRepo({
      installation_id: ctx.installationId,
      full_name: ctx.fullName,
      ref: ctx.ref,
    })
    return mapDetectedStackToFramework(result.detected_stack)
  } catch {
    return null
  }
}

export async function enhanceDockerScaffoldTargets(
  workspaceId: string,
  targets: ScaffoldTarget[],
  analyzeWorkspaceFile: (
    workspaceId: string,
    payload: { path: string; content: string; kind?: 'docker' },
  ) => Promise<WorkspaceFileAnalysisReport>,
): Promise<ScaffoldTarget[]> {
  const enhanced: ScaffoldTarget[] = []
  for (const target of targets) {
    if (!target.path.startsWith('dockers/Dockerfile')) {
      enhanced.push(target)
      continue
    }
    try {
      const report = await analyzeWorkspaceFile(workspaceId, {
        path: target.path,
        content: target.content,
        kind: 'docker',
      })
      enhanced.push({
        path: target.path,
        content: report.improvedContent?.trim() || target.content,
      })
    } catch {
      enhanced.push(target)
    }
  }
  return enhanced
}

export function buildRepoAwareDockerScaffold(
  cfg: Parameters<typeof buildDockerScaffold>[0],
): ScaffoldTarget[] {
  return buildDockerScaffold(cfg)
}

export function buildRepoScaffoldBundle(options: {
  appName: string
  infra: InfraGenerationConfig
  containerScaffold: ContainerScaffoldConfig
  detectedFramework?: FrameworkOption | null
}): ScaffoldTarget[] {
  const appName = options.appName.trim() || 'app'
  const targets: ScaffoldTarget[] = []

  let container: ContainerScaffoldConfig = {
    ...options.containerScaffold,
    app_name: appName,
  }
  if (options.detectedFramework && container.enabled) {
    const frameworks = container.frameworks?.length
      ? container.frameworks
      : [options.detectedFramework]
    container = {
      ...container,
      stack: options.detectedFramework,
      frameworks,
    }
  }

  if (container.enabled) {
    targets.push(...buildDockerScaffold(container))
  }
  if (options.infra.provision.enabled) {
    targets.push(
      ...buildProvisionScaffold(
        `scaffold-${appName}`,
        appName,
        options.infra.provision.engine,
      ),
    )
  }
  if (options.infra.kubernetes.enabled) {
    targets.push(...buildKubernetesScaffold(options.infra.kubernetes.mode))
  }
  if (options.infra.cicd.enabled) {
    const frameworks =
      options.infra.cicd.frameworks.length > 0
        ? options.infra.cicd.frameworks
        : (container.frameworks?.length
            ? container.frameworks
            : options.detectedFramework
              ? [options.detectedFramework]
              : [])
    targets.push(
      ...buildCiCdScaffold(
        options.infra.cicd.platform,
        options.infra.cicd.security,
        frameworks,
        appName,
      ),
    )
  }
  return targets
}
