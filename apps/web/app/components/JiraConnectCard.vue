<script setup lang="ts">
import type { JiraIntegrationStatus } from '~/types/integrations'

const { t } = useI18n()
const toast = useToast()
const { activeOrgId, orgs } = useOrgs()
const { getJira, saveJira, disconnectJira } = useOrgIntegrations()

const status = ref<JiraIntegrationStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const siteUrl = ref('')
const email = ref('')
const apiToken = ref('')
const projectKey = ref('')
const issueType = ref('Bug')
const autoCreate = ref(false)

const activeOrg = computed(() => orgs.value.find((o) => o.id === activeOrgId.value) ?? null)
const canManage = computed(() => {
  const role = (activeOrg.value?.role || '').toLowerCase()
  return role === 'owner' || role === 'admin'
})

function applyStatus(next: JiraIntegrationStatus) {
  status.value = next
  siteUrl.value = next.site_url || ''
  email.value = next.email || ''
  projectKey.value = next.project_key || ''
  issueType.value = next.issue_type || 'Bug'
  autoCreate.value = next.auto_create_on_failure
  apiToken.value = ''
}

async function load() {
  if (!activeOrgId.value) {
    status.value = null
    loading.value = false
    return
  }
  loading.value = true
  try {
    applyStatus(await getJira())
  } catch (err) {
    toast.error(t('integrations.jiraLoadFailed'), err instanceof Error ? err.message : t('common.failed'))
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!canManage.value) return
  saving.value = true
  try {
    const next = await saveJira({
      site_url: siteUrl.value.trim() || undefined,
      email: email.value.trim() || undefined,
      api_token: apiToken.value.trim() || undefined,
      project_key: projectKey.value.trim() || undefined,
      issue_type: issueType.value.trim() || 'Bug',
      auto_create_on_failure: autoCreate.value,
    })
    applyStatus(next)
    toast.success(t('integrations.jiraSaved'), t('integrations.jiraSavedBody'))
  } catch (err) {
    toast.error(t('integrations.jiraSaveFailed'), err instanceof Error ? err.message : t('common.failed'))
  } finally {
    saving.value = false
  }
}

async function onDisconnect() {
  if (!canManage.value) return
  saving.value = true
  try {
    applyStatus(await disconnectJira())
    toast.success(t('integrations.jiraDisconnected'), t('integrations.jiraDisconnectedBody'))
  } catch (err) {
    toast.error(t('integrations.jiraSaveFailed'), err instanceof Error ? err.message : t('common.failed'))
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
        <IntegrationBrandIcon brand="jira" />
        <div>
          <h2 class="text-lg font-semibold">{{ t('integrations.connectJira') }}</h2>
          <p class="mt-1 text-sm text-[var(--lp-muted)]">
            {{ t('integrations.connectJiraBlurb') }}
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

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block space-y-1.5 sm:col-span-2">
          <span class="lp-label">{{ t('integrations.jiraSiteUrl') }}</span>
          <input
            v-model="siteUrl"
            type="url"
            class="lp-input w-full font-mono text-sm"
            placeholder="https://your-domain.atlassian.net"
            :disabled="!canManage || saving"
          >
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">{{ t('integrations.jiraEmail') }}</span>
          <input
            v-model="email"
            type="email"
            class="lp-input w-full text-sm"
            :disabled="!canManage || saving"
          >
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">{{ t('integrations.jiraApiToken') }}</span>
          <input
            v-model="apiToken"
            type="password"
            autocomplete="off"
            class="lp-input w-full font-mono text-sm"
            :placeholder="status?.token_configured ? t('integrations.tokenConfigured') : ''"
            :disabled="!canManage || saving"
          >
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">{{ t('integrations.jiraProjectKey') }}</span>
          <input
            v-model="projectKey"
            type="text"
            class="lp-input w-full font-mono text-sm uppercase"
            placeholder="ENG"
            :disabled="!canManage || saving"
          >
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">{{ t('integrations.jiraIssueType') }}</span>
          <input
            v-model="issueType"
            type="text"
            class="lp-input w-full text-sm"
            placeholder="Bug"
            :disabled="!canManage || saving"
          >
        </label>
      </div>

      <label class="flex items-center gap-2 text-sm">
        <input v-model="autoCreate" type="checkbox" class="rounded border-[var(--lp-line)]" :disabled="!canManage || saving">
        {{ t('integrations.jiraAutoCreate') }}
      </label>

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
          {{ t('integrations.disconnectJira') }}
        </button>
      </div>
    </template>
  </section>
</template>
