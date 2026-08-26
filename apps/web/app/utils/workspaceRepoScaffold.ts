import type {
  ContainerScaffoldConfig,
  FrameworkOption,
  InfraGenerationConfig,
  WorkloadDependenciesConfig,
} from '~/types/provisioning'
import type { ProjectStack } from '~/types/dockerfileSchema'
import type {
  WorkspaceAnalysisKind,
  WorkspaceFileAnalysisReport,
} from '~/utils/workspaceFileAnalysis'
import { detectWorkspaceFileKind, isAnalyzableWorkspacePath } from '~/utils/workspaceFileAnalysis'
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

const PROJECT_STACK_TO_FRAMEWORK: Partial<Record<ProjectStack | FrameworkOption, FrameworkOption>> = {
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

const DEFAULT_ENHANCE_KINDS: WorkspaceAnalysisKind[] = ['docker', 'cicd', 'iac']

export type AnalyzeWorkspaceFileFn = (
  workspaceId: string,
  payload: {
    path: string
    content: string
    kind?: 'auto' | WorkspaceAnalysisKind
  },
) => Promise<WorkspaceFileAnalysisReport>

/** True when scaffold path is Docker, CI/CD, or Terraform/Pulumi IaC. */
export function isEnhanceableScaffoldPath(
  path: string,
  kinds: WorkspaceAnalysisKind[] = DEFAULT_ENHANCE_KINDS,
): boolean {
  const normalized = path.replace(/\\/g, '/').toLowerCase()
  const base = normalized.split('/').pop() || ''
  const kind = detectWorkspaceFileKind(path)
  if (!kinds.includes(kind)) return false
  // detectWorkspaceFileKind defaults unknown paths to iac - require real IaC markers.
  if (kind === 'iac') {
    return (
      normalized.includes('infra/terraform/')
      || normalized.includes('infra/pulumi/')
      || normalized.includes('/terraform/')
      || normalized.includes('/pulumi/')
      || base.endsWith('.tf')
      || base.endsWith('.tfvars')
      || base === 'pulumi.yaml'
      || base === 'pulumi.yml'
      || (base.endsWith('.ts') && normalized.includes('pulumi'))
    )
  }
  return true
}

/**
 * Run workspace file AI (Gemini or heuristics) on scaffold targets and apply
 * improvedContent when present. Covers Docker, CI workflows, and Terraform/IaC.
 */
export async function enhanceScaffoldTargets(
  workspaceId: string,
  targets: ScaffoldTarget[],
  analyzeWorkspaceFile: AnalyzeWorkspaceFileFn,
  kinds: WorkspaceAnalysisKind[] = DEFAULT_ENHANCE_KINDS,
): Promise<ScaffoldTarget[]> {
  const enhanced: ScaffoldTarget[] = []
  for (const target of targets) {
    if (!isEnhanceableScaffoldPath(target.path, kinds)) {
      enhanced.push(target)
      continue
    }
    const kind = detectWorkspaceFileKind(target.path)
    try {
      const report = await analyzeWorkspaceFile(workspaceId, {
        path: target.path,
        content: target.content,
        kind,
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

/** @deprecated Prefer enhanceScaffoldTargets with kinds including docker. */
export async function enhanceDockerScaffoldTargets(
  workspaceId: string,
  targets: ScaffoldTarget[],
  analyzeWorkspaceFile: AnalyzeWorkspaceFileFn,
): Promise<ScaffoldTarget[]> {
  return enhanceScaffoldTargets(workspaceId, targets, analyzeWorkspaceFile, ['docker'])
}

type WorkspaceEnhanceApi = {
  listWorkspaceFiles: (workspaceId: string) => Promise<Array<{ path: string; type: string }>>
  readWorkspaceFile: (workspaceId: string, path: string) => Promise<{ content: string }>
  writeWorkspaceFile: (workspaceId: string, path: string, content: string) => Promise<unknown>
  analyzeWorkspaceFile: AnalyzeWorkspaceFileFn
}

/**
 * AI-enhance existing workspace files (e.g. API-generated Terraform + CI after provision).
 * Returns the number of files rewritten with improvedContent.
 */
export async function enhanceExistingWorkspaceFiles(
  workspaceId: string,
  api: WorkspaceEnhanceApi,
  kinds: WorkspaceAnalysisKind[] = DEFAULT_ENHANCE_KINDS,
  maxFiles = 20,
): Promise<number> {
  let nodes: Array<{ path: string; type: string }>
  try {
    nodes = await api.listWorkspaceFiles(workspaceId)
  } catch {
    return 0
  }
  const paths = nodes
    .filter((n) => n.type === 'file')
    .map((n) => n.path)
    .filter(isAnalyzableWorkspacePath)
    .filter((p) => isEnhanceableScaffoldPath(p, kinds))
    .sort((a, b) => a.localeCompare(b))
    .slice(0, maxFiles)

  let rewritten = 0
  for (const path of paths) {
    try {
      const file = await api.readWorkspaceFile(workspaceId, path)
      const kind = detectWorkspaceFileKind(path)
      const report = await api.analyzeWorkspaceFile(workspaceId, {
        path,
        content: file.content,
        kind,
      })
      const next = report.improvedContent?.trim()
      if (!next || next === file.content.trim()) continue
      await api.writeWorkspaceFile(workspaceId, path, next.endsWith('\n') ? next : `${next}\n`)
      rewritten += 1
    } catch {
      // Keep original file when analyze/write fails.
    }
  }
  return rewritten
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
  dependencies?: WorkloadDependenciesConfig
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
    let datastores: Array<{ kind: string }> | undefined = undefined
    if (options.dependencies) {
      datastores = []
      for (const [key, val] of Object.entries(options.dependencies)) {
        if (val?.enabled) {
          datastores.push({ kind: key })
        }
      }
    }
    targets.push(...buildDockerScaffold(container, datastores))
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
