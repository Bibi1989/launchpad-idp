export type WorkspaceAnalysisKind = 'cicd' | 'docker' | 'iac' | 'kubernetes'

export interface WorkspaceFileIssue {
  title: string
  description: string
  severity: 'info' | 'warning' | 'critical'
  ruleId?: string | null
}

export interface WorkspaceFileAnalysisReport {
  kind: WorkspaceAnalysisKind
  summary: string
  issues: WorkspaceFileIssue[]
  suggestions: string[]
  improvedContent: string | null
  analysisSource: 'gemini' | 'heuristic'
}

/** Infer analysis domain from a workspace-relative path. */
export function detectWorkspaceFileKind(path: string | null | undefined): WorkspaceAnalysisKind {
  const normalized = (path || '').replace(/\\/g, '/').toLowerCase()
  const base = normalized.split('/').pop() || ''

  if (
    base === 'dockerfile'
    || base.startsWith('dockerfile.')
    || normalized.includes('/dockers/')
    || normalized.endsWith('docker-compose.yml')
    || normalized.endsWith('docker-compose.yaml')
    || normalized.endsWith('compose.yml')
    || normalized.endsWith('compose.yaml')
  ) {
    return 'docker'
  }

  if (
    normalized.includes('.github/workflows/')
    || normalized.includes('ci/github/')
    || normalized.includes('ci/gitlab/')
    || base === '.gitlab-ci.yml'
    || base.endsWith('.gitlab-ci.yml')
  ) {
    return 'cicd'
  }

  if (
    normalized.includes('infra/k8s/')
    || normalized.includes('infra/helm/')
    || normalized.includes('/manifests/')
    || normalized.startsWith('manifests/')
  ) {
    if (
      normalized.includes('/k8s/')
      || normalized.includes('infra/k8s/')
      || normalized.includes('/helm/')
      || normalized.includes('infra/helm/')
      || normalized.includes('/manifests/')
      || /^(deployment|service|ingress|hpa|vpa|pdb|namespace|configmap|secret)/.test(base)
    ) {
      return 'kubernetes'
    }
  }

  if (
    normalized.includes('infra/terraform/')
    || normalized.includes('infra/pulumi/')
    || normalized.includes('/terraform/')
    || normalized.includes('/pulumi/')
    || base.endsWith('.tf')
    || base.endsWith('.tfvars')
    || base === 'pulumi.yaml'
    || base === 'pulumi.yml'
    || (base.endsWith('.ts') && normalized.includes('pulumi'))
  ) {
    return 'iac'
  }

  if (base.endsWith('.tf') || base.endsWith('.tfvars')) return 'iac'
  if (/\.(ya?ml)$/.test(base)) return 'kubernetes'
  return 'iac'
}

export function analysisKindLabel(kind: WorkspaceAnalysisKind): string {
  switch (kind) {
    case 'cicd':
      return 'CI/CD'
    case 'docker':
      return 'Docker'
    case 'iac':
      return 'IaC'
    case 'kubernetes':
      return 'Kubernetes'
    default:
      return kind
  }
}

const ANALYZABLE_EXT = new Set([
  '.tf',
  '.tfvars',
  '.yml',
  '.yaml',
  '.json',
  '.ts',
  '.js',
  '.py',
  '.toml',
  '.hcl',
  '.dockerfile',
])

const SKIP_DIR_SEGMENTS = new Set([
  '.terraform',
  'node_modules',
  '.git',
  '__pycache__',
  'dist',
  '.pulumi',
])

/** True when a workspace path is suitable for AI infra analysis. */
export function isAnalyzableWorkspacePath(path: string): boolean {
  const normalized = path.replace(/\\/g, '/').replace(/^\.\//, '')
  const parts = normalized.split('/')
  if (parts.some((p) => SKIP_DIR_SEGMENTS.has(p))) return false
  const base = parts[parts.length - 1] || ''
  const lower = base.toLowerCase()
  if (lower === 'dockerfile' || lower.startsWith('dockerfile.')) return true
  if (lower === 'pulumi.yaml' || lower === 'pulumi.yml') return true
  if (lower === '.gitlab-ci.yml' || lower.endsWith('.gitlab-ci.yml')) return true
  if (lower === 'compose.yml' || lower === 'compose.yaml') return true
  if (lower.endsWith('docker-compose.yml') || lower.endsWith('docker-compose.yaml')) return true
  const dot = lower.lastIndexOf('.')
  if (dot < 0) return false
  return ANALYZABLE_EXT.has(lower.slice(dot))
}

/** Collect file paths under a folder (or a single file) for batch AI analysis. */
export function collectAnalyzablePaths(
  nodes: Array<{ path: string; type: 'file' | 'directory' }>,
  selectedPath: string,
  maxFiles = 20,
): string[] {
  const normalized = selectedPath.replace(/\\/g, '/').replace(/\/$/, '')
  const selected = nodes.find((n) => n.path === normalized || n.path === selectedPath)
  if (!selected) return []
  if (selected.type === 'file') {
    return isAnalyzableWorkspacePath(selected.path) ? [selected.path] : []
  }
  const prefix = `${normalized}/`
  return nodes
    .filter((n) => n.type === 'file' && (n.path === normalized || n.path.startsWith(prefix)))
    .map((n) => n.path)
    .filter(isAnalyzableWorkspacePath)
    .sort((a, b) => a.localeCompare(b))
    .slice(0, maxFiles)
}
