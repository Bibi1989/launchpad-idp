<script setup lang="ts">
import type { OrgPromotionPolicy, PromotionRequest } from '~/types/environment'

const { t } = useI18n()
const { orgs, activeOrgId } = useOrgs()
const { getPolicy, updatePolicy, listPromotions, approve, reject } = usePromotions()
const toast = useToast()

const orgId = computed(() => activeOrgId.value || orgs.value[0]?.id || null)
const activeOrg = computed(() => orgs.value.find((org) => org.id === orgId.value) || null)
const canAdmin = computed(() => {
  const role = activeOrg.value?.role
  return role === 'owner' || role === 'admin'
})

const policy = ref<OrgPromotionPolicy | null>(null)
const rows = ref<PromotionRequest[]>([])
const loading = ref(false)
const savingPolicy = ref(false)
const actingId = ref<string | null>(null)
const error = ref<string | null>(null)
const statusFilter = ref<'pending' | 'all'>('pending')

async function load() {
  if (!orgId.value) return
  loading.value = true
  error.value = null
  try {
    const [policyRow, list] = await Promise.all([
      getPolicy(orgId.value),
      listPromotions(orgId.value, statusFilter.value === 'pending' ? 'pending' : undefined),
    ])
    policy.value = policyRow
    rows.value = list
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    loading.value = false
  }
}

async function savePolicy() {
  if (!orgId.value || !policy.value || !canAdmin.value || savingPolicy.value) return
  savingPolicy.value = true
  error.value = null
  try {
    policy.value = await updatePolicy(orgId.value, {
      staging_requires_approval: policy.value.staging_requires_approval,
      production_requires_approval: policy.value.production_requires_approval,
    })
    toast.success(t('promotions.policySaved'), t('promotions.policySavedBlurb'))
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    savingPolicy.value = false
  }
}

async function onApprove(row: PromotionRequest) {
  if (!canAdmin.value || actingId.value) return
  actingId.value = row.id
  try {
    const result = await approve(row.id)
    toast.success(t('promotions.approved'), t('promotions.approvedBlurb'))
    if (result.environment_id) {
      await navigateTo(`/environments/${result.environment_id}`)
      return
    }
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    actingId.value = null
  }
}

async function onReject(row: PromotionRequest) {
  if (!canAdmin.value || actingId.value) return
  actingId.value = row.id
  try {
    await reject(row.id)
    toast.info(t('promotions.rejected'), t('promotions.rejectedBlurb'))
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    actingId.value = null
  }
}

watch([orgId, statusFilter], () => {
  void load()
}, { immediate: true })
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <header class="space-y-2">
      <NuxtLink
        to="/org"
        class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
      >
        <span class="material-symbols-outlined text-sm">arrow_back</span>
        {{ t('promotions.backToOrg') }}
      </NuxtLink>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('promotions.title') }}</h1>
      <p class="max-w-2xl text-sm text-[var(--lp-muted)]">
        {{ t('promotions.blurb') }}
      </p>
    </header>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <AppSplash v-if="loading && !policy" compact :message="t('common.loading')" />

    <section
      v-if="policy"
      class="lp-glass space-y-4 rounded-xl border border-[var(--lp-line)] p-5"
    >
      <h2 class="text-lg font-semibold">{{ t('promotions.policyTitle') }}</h2>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('promotions.policyBlurb') }}</p>
      <label class="flex items-center gap-3 text-sm">
        <input
          v-model="policy.staging_requires_approval"
          type="checkbox"
          class="rounded border-[var(--lp-line)]"
          :disabled="!canAdmin || savingPolicy"
        >
        {{ t('promotions.stagingRequiresApproval') }}
      </label>
      <label class="flex items-center gap-3 text-sm">
        <input
          v-model="policy.production_requires_approval"
          type="checkbox"
          class="rounded border-[var(--lp-line)]"
          :disabled="!canAdmin || savingPolicy"
        >
        {{ t('promotions.productionRequiresApproval') }}
      </label>
      <button
        v-if="canAdmin"
        type="button"
        class="lp-btn-primary"
        :disabled="savingPolicy"
        @click="savePolicy"
      >
        {{ savingPolicy ? t('common.saving') : t('promotions.savePolicy') }}
      </button>
      <p v-else class="text-xs text-[var(--lp-muted)]">{{ t('promotions.adminOnly') }}</p>
    </section>

    <section class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-lg font-semibold">{{ t('promotions.queueTitle') }}</h2>
        <div class="flex gap-2">
          <button
            type="button"
            class="rounded-lg border px-3 py-1.5 text-sm"
            :class="statusFilter === 'pending' ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10' : 'border-[var(--lp-line)]'"
            @click="statusFilter = 'pending'"
          >
            {{ t('promotions.filterPending') }}
          </button>
          <button
            type="button"
            class="rounded-lg border px-3 py-1.5 text-sm"
            :class="statusFilter === 'all' ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10' : 'border-[var(--lp-line)]'"
            @click="statusFilter = 'all'"
          >
            {{ t('promotions.filterAll') }}
          </button>
        </div>
      </div>

      <p v-if="!loading && rows.length === 0" class="text-sm text-[var(--lp-muted)]">
        {{ t('promotions.empty') }}
      </p>

      <ul class="space-y-3">
        <li
          v-for="row in rows"
          :key="row.id"
          class="lp-glass flex flex-col gap-3 rounded-xl border border-[var(--lp-line)] p-4 sm:flex-row sm:items-center sm:justify-between"
        >
          <div class="min-w-0 space-y-1">
            <p class="font-medium">
              {{ row.source_environment_name || row.source_environment_id }}
              <span class="text-[var(--lp-muted)]">→</span>
              <LifecycleStageBadge :stage="row.target_stage" />
            </p>
            <p class="font-mono text-xs text-[var(--lp-muted)]">
              {{ row.status }} · {{ new Date(row.created_at).toLocaleString() }}
            </p>
            <NuxtLink
              :to="`/environments/${row.source_environment_id}`"
              class="text-xs text-[var(--lp-accent)] hover:underline"
            >
              {{ t('promotions.openSource') }}
            </NuxtLink>
          </div>
          <div v-if="row.status === 'pending' && canAdmin" class="flex flex-wrap gap-2">
            <button
              type="button"
              class="lp-btn-primary"
              :disabled="actingId === row.id"
              @click="onApprove(row)"
            >
              {{ t('promotions.approve') }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost"
              :disabled="actingId === row.id"
              @click="onReject(row)"
            >
              {{ t('promotions.reject') }}
            </button>
          </div>
          <NuxtLink
            v-else-if="row.target_environment_id"
            :to="`/environments/${row.target_environment_id}`"
            class="lp-btn-ghost"
          >
            {{ t('promotions.openTarget') }}
          </NuxtLink>
        </li>
      </ul>
    </section>
  </div>
</template>
