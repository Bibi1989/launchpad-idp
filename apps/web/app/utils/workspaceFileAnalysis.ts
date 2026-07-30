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
