import type { OrgPlanSummary } from '~/types/auth'

export function useBilling() {
  const { apiFetch } = useApi()

  async function getPlan(orgId: string): Promise<OrgPlanSummary> {
    return apiFetch<OrgPlanSummary>(`/billing/orgs/${orgId}/plan`)
  }

  async function startCheckout(orgId: string): Promise<string> {
    const result = await apiFetch<{ checkout_url: string }>(
      `/billing/orgs/${orgId}/checkout`,
      { method: 'POST' },
    )
    return result.checkout_url
  }

  async function openPortal(orgId: string): Promise<string> {
    const result = await apiFetch<{ portal_url: string }>(
      `/billing/orgs/${orgId}/portal`,
      { method: 'POST' },
    )
    return result.portal_url
  }

  return { getPlan, startCheckout, openPortal }
}
