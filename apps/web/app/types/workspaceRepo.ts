import type { WorkspaceCdMode } from '~/types/provisioning'

/** Queued GitHub App link applied after the workspace is created. */
export interface PendingGithubAppLink {
  kind: 'github'
  installation_id: number
  full_name: string
  git_branch: string
  cd_mode: WorkspaceCdMode
}

/** Queued GitLab tracking applied after the workspace is created. */
export interface PendingGitlabGitSource {
  kind: 'gitlab'
  git_repo_url: string
  git_branch: string
}

export type PendingWorkspaceRepoLink = PendingGithubAppLink | PendingGitlabGitSource

export type WorkspaceSourceMode = 'link' | 'import' | 'services'
