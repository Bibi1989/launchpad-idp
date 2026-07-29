import type {
  OrgCostSummary,
  OrgInvite,
  OrgMember,
  OrgSsoMapping,
  OrgSummary,
} from '~/types/auth'

const ORG_KEY = 'launchpad_active_org_id'

export function useOrgs() {
  const { apiFetch } = useApi()
  const orgs = useState<OrgSummary[]>('orgs', () => [])
  const activeOrgId = useState<string | null>('active-org-id', () => null)

  function loadActiveOrgFromStorage() {
    if (!import.meta.client) return
    const stored = localStorage.getItem(ORG_KEY)
    if (stored) activeOrgId.value = stored
  }

  function setActiveOrg(orgId: string | null) {
    activeOrgId.value = orgId
    if (!import.meta.client) return
    if (orgId) localStorage.setItem(ORG_KEY, orgId)
    else localStorage.removeItem(ORG_KEY)
  }

  function applyFromTokenResponse(payload: {
    orgs?: OrgSummary[]
    active_org_id?: string | null
  }) {
    if (payload.orgs) orgs.value = payload.orgs
    if (payload.active_org_id) setActiveOrg(payload.active_org_id)
    else if (!activeOrgId.value && payload.orgs?.[0]) setActiveOrg(payload.orgs[0].id)

    if (
      activeOrgId.value
      && payload.orgs?.length
      && !payload.orgs.some((org) => org.id === activeOrgId.value)
    ) {
      setActiveOrg(payload.orgs[0]?.id ?? null)
    }
  }

  async function fetchOrgCosts(orgId?: string | null): Promise<OrgCostSummary | null> {
    const id = orgId ?? activeOrgId.value
    if (!id) return null
    return apiFetch<OrgCostSummary>(`/orgs/${id}/costs`)
  }

  async function listMembers(orgId: string): Promise<OrgMember[]> {
    return apiFetch<OrgMember[]>(`/orgs/${orgId}/members`)
  }

  async function listInvites(orgId: string): Promise<OrgInvite[]> {
    return apiFetch<OrgInvite[]>(`/orgs/${orgId}/invites`)
  }

  async function createInvite(
    orgId: string,
    payload: { email: string; role?: string },
  ): Promise<OrgInvite> {
    return apiFetch<OrgInvite>(`/orgs/${orgId}/invites`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async function revokeInvite(orgId: string, inviteId: string): Promise<void> {
    await apiFetch(`/orgs/${orgId}/invites/${inviteId}`, { method: 'DELETE' })
  }

  async function acceptInvite(token: string): Promise<OrgMember> {
    return apiFetch<OrgMember>('/orgs/invites/accept', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
  }

  async function listSsoMappings(orgId: string): Promise<OrgSsoMapping[]> {
    return apiFetch<OrgSsoMapping[]>(`/orgs/${orgId}/sso-mappings`)
  }

  async function upsertSsoMapping(
    orgId: string,
    payload: { group_name: string; role?: string },
  ): Promise<OrgSsoMapping> {
    return apiFetch<OrgSsoMapping>(`/orgs/${orgId}/sso-mappings`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async function deleteSsoMapping(orgId: string, mappingId: string): Promise<void> {
    await apiFetch(`/orgs/${orgId}/sso-mappings/${mappingId}`, { method: 'DELETE' })
  }

  return {
    orgs,
    activeOrgId,
    loadActiveOrgFromStorage,
    setActiveOrg,
    applyFromTokenResponse,
    fetchOrgCosts,
    listMembers,
    listInvites,
    createInvite,
    revokeInvite,
    acceptInvite,
    listSsoMappings,
    upsertSsoMapping,
    deleteSsoMapping,
  }
}
