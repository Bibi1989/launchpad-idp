export interface PendingInvite {
  kind: 'org' | 'project'
  invite_id: string
  role: string
  org_id: string
  org_name: string
  project_id?: string | null
  project_name?: string | null
  invited_by?: string | null
  expires_at: string
  created_at: string
  href: string
}

export function usePendingInvites() {
  const { apiFetch } = useApi()

  async function listPending(): Promise<PendingInvite[]> {
    return apiFetch<PendingInvite[]>('/invites/pending')
  }

  async function acceptOrgInvite(inviteId: string) {
    return apiFetch<{
      user_id: string
      email: string
      display_name: string
      role: string
      org_id?: string | null
      org_name?: string | null
    }>(`/invites/org/${inviteId}/accept`, { method: 'POST' })
  }

  async function acceptProjectInvite(inviteId: string) {
    return apiFetch<{
      project_id: string
      project_name: string
      org_id: string
      org_name: string
      role: string
    }>(`/invites/project/${inviteId}/accept`, { method: 'POST' })
  }

  return {
    listPending,
    acceptOrgInvite,
    acceptProjectInvite,
  }
}
