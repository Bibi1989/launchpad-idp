import type { CicdPlatform, FrameworkOption } from '~/types/provisioning'
import {
  defaultCicdSecurityConfig,
  inferCicdSecurityFromContent,
  type CicdSecurityConfig,
} from '~/utils/cicdWorkflowGenerator'
import {
  buildCiCdScaffold,
  detectCicdPlatformFromPaths,
  oppositeCicdFilePaths,
} from '~/utils/workspaceInfraScaffold'

type FileApi = {
  listWorkspaceFiles: (workspaceId: string) => Promise<Array<{ path: string; type: string }>>
  readWorkspaceFile: (workspaceId: string, path: string) => Promise<{ content: string }>
  writeWorkspaceFile: (workspaceId: string, path: string, content: string) => Promise<unknown>
  deleteWorkspacePath: (workspaceId: string, path: string) => Promise<void>
}

/**
 * Ensure workspace CI matches the selected platform: write target files and
 * remove the opposite provider's CI paths.
 */
export async function syncWorkspaceCicdToPlatform(
  api: FileApi,
  workspaceId: string,
  target: CicdPlatform,
  opts: {
    appName?: string
    frameworks?: FrameworkOption[]
    security?: CicdSecurityConfig
  } = {},
): Promise<{ converted: boolean; wrote: string[]; removed: string[] }> {
  const nodes = await api.listWorkspaceFiles(workspaceId)
  const paths = nodes.filter((n) => n.type === 'file').map((n) => n.path)
  const current = detectCicdPlatformFromPaths(paths)

  let security = opts.security ?? defaultCicdSecurityConfig(target)
  if (!opts.security && current) {
    const sourcePaths = paths.filter((p) =>
      current === 'github'
        ? p.startsWith('ci/github/') || p.startsWith('.github/workflows/')
        : p.startsWith('ci/gitlab/') || p === '.gitlab-ci.yml' || p.endsWith('/.gitlab-ci.yml'),
    )
    const first = sourcePaths[0]
    if (first) {
      try {
        const file = await api.readWorkspaceFile(workspaceId, first)
        security = inferCicdSecurityFromContent(file.content)
      } catch {
        // keep default security
      }
    }
  }

  const targets = buildCiCdScaffold(
    target,
    security,
    opts.frameworks ?? [],
    opts.appName ?? 'app',
  )
  const wrote: string[] = []
  for (const targetFile of targets) {
    await api.writeWorkspaceFile(workspaceId, targetFile.path, targetFile.content)
    wrote.push(targetFile.path)
  }

  const removed: string[] = []
  for (const path of oppositeCicdFilePaths(target, paths)) {
    // Don't delete a path we just wrote (shouldn't overlap, but be safe).
    if (wrote.includes(path)) continue
    try {
      await api.deleteWorkspacePath(workspaceId, path)
      removed.push(path)
    } catch {
      // ignore missing/locked paths
    }
  }

  return {
    converted: current !== null && current !== target,
    wrote,
    removed,
  }
}
