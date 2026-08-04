import type { GitHubInstallationItem } from '~/types/provisioning'

/** Human label for a GitHub App installation account type. */
export function githubAccountTypeLabel(item: GitHubInstallationItem): string {
  const raw = (item.account_type || item.target_type || '').toLowerCase()
  if (raw === 'organization' || raw === 'org') return 'Organization'
  if (raw === 'user') return 'Personal'
  return item.account_type || 'GitHub'
}

export function isPersonalGithubInstallation(item: GitHubInstallationItem | null | undefined): boolean {
  if (!item) return false
  return (item.account_type || item.target_type || '').toLowerCase() === 'user'
}

/** HTTPS clone URL for a GitHub full name (`owner/repo`). */
export function githubCloneUrl(fullName: string): string {
  const cleaned = fullName.trim().replace(/\.git$/i, '')
  return `https://github.com/${cleaned}.git`
}
