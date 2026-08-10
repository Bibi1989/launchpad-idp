<script setup lang="ts">
import type { SlackIntegrationStatus } from '~/types/integrations'

const { t } = useI18n()
const toast = useToast()
const { activeOrgId, orgs } = useOrgs()
const { getSlack, saveSlack, disconnectSlack } = useOrgIntegrations()

const status = ref<SlackIntegrationStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const webhookUrl = ref('')
const notifyReady = ref(true)
const notifyFailed = ref(true)
const notifyTtl = ref(true)
const notifyCost = ref(true)

const activeOrg = computed(() => orgs.value.find((o) => o.id === activeOrgId.value) ?? null)
const canManage = computed(() => {
  const role = (activeOrg.value?.role || '').toLowerCase()
  return role === 'owner' || role === 'admin'
})

function applyStatus(next: SlackIntegrationStatus) {
  status.value = next
  notifyReady.value = next.notify_ready
  notifyFailed.value = next.notify_failed
  notifyTtl.value = next.notify_ttl_warning
  notifyCost.value = next.notify_cost_cap
  if (!next.connected) webhookUrl.value = ''
}

async function load() {
  if (!activeOrgId.value) {
    status.value = null
    loading.value = false
    return
  }
  loading.value = true
  try {
    applyStatus(await getSlack())
  } catch (err) {
    toast.error(t('integrations.slackLoadFailed'), err instanceof Error ? err.message : t('common.failed'))
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!canManage.value) return
  saving.value = true
  try {
    const next = await saveSlack({
      webhook_url: webhookUrl.value.trim() || undefined,
      notify_ready: notifyReady.value,
      notify_failed: notifyFailed.value,
      notify_ttl_warning: notifyTtl.value,
      notify_cost_cap: notifyCost.value,
    })
    applyStatus(next)
    webhookUrl.value = ''
    toast.success(t('integrations.slackSaved'), t('integrations.slackSavedBody'))
  } catch (err) {
    toast.error(t('integrations.slackSaveFailed'), err instanceof Error ? err.message : t('common.failed'))
  } finally {
    saving.value = false
  }
}

async function onDisconnect() {
  if (!canManage.value) return
  saving.value = true
  try {
    applyStatus(await disconnectSlack())
    toast.success(t('integrations.slackDisconnected'), t('integrations.slackDisconnectedBody'))
  } catch (err) {
    toast.error(t('integrations.slackSaveFailed'), err instanceof Error ? err.message : t('common.failed'))
  } finally {
    saving.value = false
  }
}

watch(activeOrgId, () => {
  void load()
})

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="lp-panel space-y-5 p-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="flex items-start gap-3">
        <IntegrationBrandIcon brand="slack" />
        <div>
          <h2 class="text-lg font-semibold">{{ t('integrations.connectSlack') }}</h2>
          <p class="mt-1 text-sm text-[var(--lp-muted)]">
            {{ t('integrations.connectSlackBlurb') }}
          </p>
        </div>
      </div>
      <span
        v-if="status?.connected"
        class="rounded-md border border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/10 px-2 py-1 text-xs font-medium text-[var(--lp-accent)]"
      >
        {{ t('integrations.connected') }}
      </span>
    </div>

    <p v-if="!activeOrgId" class="text-sm text-[var(--lp-warn)]">
      {{ t('integrations.selectOrg') }}
    </p>
    <AppSplash v-else-if="loading" compact :message="t('integrations.loading')" />

    <template v-else>
      <p v-if="!canManage" class="text-sm text-[var(--lp-muted)]">
        {{ t('integrations.adminOnly') }}
      </p>

      <label class="block space-y-1.5">
        <span class="lp-label">{{ t('integrations.slackWebhookUrl') }}</span>
        <input
          v-model="webhookUrl"
          type="password"
          autocomplete="off"
          class="lp-input w-full font-mono text-sm"
          :placeholder="status?.connected ? t('integrations.webhookConfigured') : 'https://hooks.slack.com/services/...'"
          :disabled="!canManage || saving"
        >
        <span class="text-xs text-[var(--lp-muted)]">{{ t('integrations.slackWebhookHint') }}</span>
      </label>

      <fieldset class="space-y-2">
        <legend class="lp-label">{{ t('integrations.slackEvents') }}</legend>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="notifyReady" type="checkbox" class="rounded border-[var(--lp-line)]" :disabled="!canManage || saving">
          {{ t('integrations.eventReady') }}
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="notifyFailed" type="checkbox" class="rounded border-[var(--lp-line)]" :disabled="!canManage || saving">
          {{ t('integrations.eventFailed') }}
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="notifyTtl" type="checkbox" class="rounded border-[var(--lp-line)]" :disabled="!canManage || saving">
          {{ t('integrations.eventTtl') }}
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="notifyCost" type="checkbox" class="rounded border-[var(--lp-line)]" :disabled="!canManage || saving">
          {{ t('integrations.eventCost') }}
        </label>
      </fieldset>

      <div class="flex flex-wrap gap-3">
        <button
          type="button"
          class="rounded-lg bg-[var(--lp-accent)] px-4 py-2 text-sm font-semibold text-[var(--lp-ink)] disabled:opacity-50"
          :disabled="!canManage || saving"
          @click="onSave"
        >
          {{ saving ? t('common.saving') : t('common.save') }}
        </button>
        <button
          v-if="status?.connected"
          type="button"
          class="rounded-lg border border-[var(--lp-line)] px-4 py-2 text-sm disabled:opacity-50"
          :disabled="!canManage || saving"
          @click="onDisconnect"
        >
          {{ t('integrations.disconnectSlack') }}
        </button>
      </div>
    </template>
  </section>
</template>
