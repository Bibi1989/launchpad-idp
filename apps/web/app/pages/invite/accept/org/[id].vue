<script setup lang="ts">
definePageMeta({ layout: false })

const { t } = useI18n()
const route = useRoute()
const { token, refreshMe } = useAuth()
const { setActiveOrg, orgs } = useOrgs()
const { acceptOrgInvite } = usePendingInvites()

const inviteId = computed(() => String(route.params.id || ''))
const status = ref<'ready' | 'working' | 'ok' | 'error'>('ready')
const message = ref('')
const orgId = ref<string | null>(null)

async function accept() {
  if (!inviteId.value || status.value === 'working') return
  status.value = 'working'
  message.value = ''
  try {
    const member = await acceptOrgInvite(inviteId.value)
    if (member.org_id) {
      orgId.value = member.org_id
      setActiveOrg(member.org_id)
      if (member.org_name && !orgs.value.some((org) => org.id === member.org_id)) {
        orgs.value = [
          ...orgs.value,
          {
            id: member.org_id,
            slug: member.org_id.slice(0, 8),
            name: member.org_name,
            role: member.role,
          },
        ]
      }
    }
    await refreshMe()
    status.value = 'ok'
    message.value = t('invite.joinedAs', {
      role: member.role,
      org: member.org_name || t('nav.organization'),
    })
  } catch (err) {
    status.value = 'error'
    message.value = err instanceof Error ? err.message : t('invite.failed')
  }
}

onMounted(() => {
  if (!import.meta.client) return
  if (!token.value) {
    const next = `/invite/accept/org/${encodeURIComponent(inviteId.value)}`
    void navigateTo(`/login?mode=register&next=${encodeURIComponent(next)}`)
  }
})
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-lg flex-col justify-center space-y-6 px-6 py-16 animate-fade-up">
    <div class="flex justify-center">
      <BrandLogo size="lg" />
    </div>
    <h1 class="text-3xl font-semibold tracking-tight">{{ t('invite.title') }}</h1>
    <p class="text-sm text-[var(--lp-muted)]">{{ t('invite.blurb') }}</p>
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
      {{ status === 'working' ? t('invite.working') : t('invite.acceptOrg') }}
    </button>
    <button
      v-else-if="status === 'ok'"
      type="button"
      class="lp-btn-primary w-full"
      @click="navigateTo(orgId ? '/org' : '/home')"
    >
      {{ t('invite.continueOrg') }}
    </button>
  </div>
</template>
