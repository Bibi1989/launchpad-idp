<script setup lang="ts">
definePageMeta({ layout: false })

const { t } = useI18n()
const route = useRoute()
const { token, refreshMe } = useAuth()
const { setActiveOrg } = useOrgs()
const { acceptProjectInvite } = usePendingInvites()

const inviteId = computed(() => String(route.params.id || ''))
const status = ref<'ready' | 'working' | 'ok' | 'error'>('ready')
const message = ref('')
const projectId = ref<string | null>(null)

async function accept() {
  if (!inviteId.value || status.value === 'working') return
  status.value = 'working'
  message.value = ''
  try {
    const result = await acceptProjectInvite(inviteId.value)
    setActiveOrg(result.org_id)
    projectId.value = result.project_id
    await refreshMe()
    status.value = 'ok'
    message.value = t('invite.projectJoined', {
      project: result.project_name,
      role: result.role,
    })
  } catch (err) {
    status.value = 'error'
    message.value = err instanceof Error ? err.message : t('invite.failed')
  }
}

onMounted(() => {
  if (!import.meta.client) return
  if (!token.value) {
    const next = `/invite/accept/project/${encodeURIComponent(inviteId.value)}`
    void navigateTo(`/login?mode=register&next=${encodeURIComponent(next)}`)
  }
})
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-lg flex-col justify-center space-y-6 px-6 py-16 animate-fade-up">
    <div class="flex justify-center">
      <BrandLogo size="lg" />
    </div>
    <h1 class="text-3xl font-semibold tracking-tight">{{ t('invite.projectTitle') }}</h1>
    <p class="text-sm text-[var(--lp-muted)]">{{ t('invite.projectBlurb') }}</p>
    <p
      v-if="message"
      class="rounded-lg border px-4 py-3 text-sm"
      :class="
        status === 'error'
          ? 'border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 text-[var(--lp-danger)]'
          : 'border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 text-[var(--lp-ok)]'
      "
    >
      {{ message }}
    </p>
    <button
      v-if="token && status !== 'ok'"
      type="button"
      class="lp-btn-primary w-full"
      :disabled="status === 'working'"
      @click="accept"
    >
      {{ status === 'working' ? t('invite.working') : t('invite.acceptProject') }}
    </button>
    <button
      v-else-if="status === 'ok'"
      type="button"
      class="lp-btn-primary w-full"
      @click="navigateTo(projectId ? `/projects/${projectId}` : '/projects')"
    >
      {{ t('invite.continueProject') }}
    </button>
  </div>
</template>
