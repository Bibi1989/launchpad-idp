import type {
  OrgPromotionPolicy,
  PromotionRequest,
  StagePromotePayload,
  StagePromoteResponse,
} from '~/types/environment'

export function usePromotions() {
  const { apiFetch } = useApi()

  async function getPolicy(orgId: string): Promise<OrgPromotionPolicy> {
    return apiFetch<OrgPromotionPolicy>(`/orgs/${orgId}/promotion-policy`)
  }

  async function updatePolicy(
    orgId: string,
    payload: Partial<OrgPromotionPolicy>,
  ): Promise<OrgPromotionPolicy> {
    return apiFetch<OrgPromotionPolicy>(`/orgs/${orgId}/promotion-policy`, {
      method: 'PATCH',
      body: payload,
    })
  }

  async function listPromotions(
    orgId: string,
    status?: string,
  ): Promise<PromotionRequest[]> {
    const q = status ? `?status=${encodeURIComponent(status)}` : ''
    return apiFetch<PromotionRequest[]>(`/orgs/${orgId}/promotions${q}`)
  }

  async function stagePromote(
    environmentId: string,
    payload: StagePromotePayload,
  ): Promise<StagePromoteResponse> {
    return apiFetch<StagePromoteResponse>(
      `/environments/${environmentId}/stage-promote`,
      {
        method: 'POST',
        body: payload as unknown as Record<string, unknown>,
        timeoutMs: 120_000,
      },
    )
  }

  async function approve(
    promotionId: string,
    note?: string,
  ): Promise<StagePromoteResponse> {
    return apiFetch<StagePromoteResponse>(`/promotions/${promotionId}/approve`, {
      method: 'POST',
      body: { note: note || null },
      timeoutMs: 120_000,
    })
  }

  async function reject(promotionId: string, note?: string): Promise<PromotionRequest> {
    return apiFetch<PromotionRequest>(`/promotions/${promotionId}/reject`, {
      method: 'POST',
      body: { note: note || null },
    })
  }

  return {
    getPolicy,
    updatePolicy,
    listPromotions,
    stagePromote,
    approve,
    reject,
  }
}
