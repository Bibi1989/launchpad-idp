import type {
  JiraIntegrationStatus,
  JiraIntegrationUpdate,
  JiraIssueRead,
  SlackIntegrationStatus,
  SlackIntegrationUpdate,
} from '~/types/integrations'

export function useOrgIntegrations() {
  const { apiFetch } = useApi()
  const { activeOrgId } = useOrgs()

  function requireOrgId(orgId?: string | null): string {
    const id = orgId ?? activeOrgId.value
    if (!id) throw new Error('Select an organization first')
    return id
  }

  async function getSlack(orgId?: string | null): Promise<SlackIntegrationStatus> {
    const id = requireOrgId(orgId)
    return apiFetch<SlackIntegrationStatus>(`/integrations/orgs/${id}/slack`)
  }

  async function saveSlack(
    payload: SlackIntegrationUpdate,
    orgId?: string | null,
  ): Promise<SlackIntegrationStatus> {
    const id = requireOrgId(orgId)
    return apiFetch<SlackIntegrationStatus>(`/integrations/orgs/${id}/slack`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async function disconnectSlack(orgId?: string | null): Promise<SlackIntegrationStatus> {
    const id = requireOrgId(orgId)
    return apiFetch<SlackIntegrationStatus>(`/integrations/orgs/${id}/slack`, {
      method: 'DELETE',
    })
  }

  async function getJira(orgId?: string | null): Promise<JiraIntegrationStatus> {
    const id = requireOrgId(orgId)
    return apiFetch<JiraIntegrationStatus>(`/integrations/orgs/${id}/jira`)
  }

  async function saveJira(
    payload: JiraIntegrationUpdate,
    orgId?: string | null,
  ): Promise<JiraIntegrationStatus> {
    const id = requireOrgId(orgId)
    return apiFetch<JiraIntegrationStatus>(`/integrations/orgs/${id}/jira`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async function disconnectJira(orgId?: string | null): Promise<JiraIntegrationStatus> {
    const id = requireOrgId(orgId)
    return apiFetch<JiraIntegrationStatus>(`/integrations/orgs/${id}/jira`, {
      method: 'DELETE',
    })
  }

  async function createOrLinkJiraIssue(
    environmentId: string,
    payload: { summary?: string; link_only_key?: string } = {},
  ): Promise<JiraIssueRead> {
    return apiFetch<JiraIssueRead>(`/integrations/environments/${environmentId}/jira-issue`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  return {
    getSlack,
    saveSlack,
    disconnectSlack,
    getJira,
    saveJira,
    disconnectJira,
    createOrLinkJiraIssue,
  }
}
