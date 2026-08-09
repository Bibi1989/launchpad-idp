import type {
  ProjectInvite,
  ProjectInviteAcceptResult,
  ProjectMember,
  ProjectSummary,
} from '~/types/auth'

export function useProjects() {
  const { apiFetch } = useApi()
  const projects = useState<ProjectSummary[]>('projects', () => [])

  async function listProjects(): Promise<ProjectSummary[]> {
    const rows = await apiFetch<ProjectSummary[]>('/projects')
    projects.value = rows
    return rows
  }

  async function createProject(payload: {
    name: string
    slug?: string
  }): Promise<ProjectSummary> {
    const created = await apiFetch<ProjectSummary>('/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    projects.value = [...projects.value, created]
    return created
  }

  async function renameProject(projectId: string, name: string): Promise<ProjectSummary> {
    const updated = await apiFetch<ProjectSummary>(`/projects/${projectId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
    projects.value = projects.value.map((p) => (p.id === projectId ? updated : p))
    return updated
  }

  async function getProject(projectId: string): Promise<ProjectSummary> {
    return apiFetch<ProjectSummary>(`/projects/${projectId}`)
  }

  async function listMembers(projectId: string): Promise<ProjectMember[]> {
    return apiFetch<ProjectMember[]>(`/projects/${projectId}/members`)
  }

  async function updateMember(
    projectId: string,
    userId: string,
    role: string,
  ): Promise<ProjectMember> {
    return apiFetch<ProjectMember>(`/projects/${projectId}/members/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    })
  }

  async function listInvites(projectId: string): Promise<ProjectInvite[]> {
    return apiFetch<ProjectInvite[]>(`/projects/${projectId}/invites`)
  }

  async function createInvite(
    projectId: string,
    payload: { email: string; role?: string },
  ): Promise<ProjectInvite> {
    return apiFetch<ProjectInvite>(`/projects/${projectId}/invites`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async function revokeInvite(projectId: string, inviteId: string): Promise<void> {
    await apiFetch(`/projects/${projectId}/invites/${inviteId}`, { method: 'DELETE' })
  }

  async function acceptInvite(token: string): Promise<ProjectInviteAcceptResult> {
    return apiFetch<ProjectInviteAcceptResult>('/projects/invites/accept', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
  }

  return {
    projects,
    listProjects,
    createProject,
    renameProject,
    getProject,
    listMembers,
    updateMember,
    listInvites,
    createInvite,
    revokeInvite,
    acceptInvite,
  }
}
