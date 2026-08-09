<script setup lang="ts">
definePageMeta({ layout: false })

const { t } = useI18n()
const route = useRoute()
const { token, refreshMe } = useAuth()
const { acceptInvite, setActiveOrg, orgs } = useOrgs()

const inviteToken = computed(() => String(route.params.token || ''))
const status = ref<'ready' | 'working' | 'ok' | 'error'>('ready')
const message = ref('')

const loginRegisterUrl = computed(() => {
  const next = `/invite/${encodeURIComponent(inviteToken.value)}`
  return `/login?mode=register&next=${encodeURIComponent(next)}`
})

const loginUrl = computed(() => {
  const next = `/invite/${encodeURIComponent(inviteToken.value)}`
  return `/login?mode=login&next=${encodeURIComponent(next)}`
})

async function accept() {
  if (!inviteToken.value || status.value === 'working') return
  status.value = 'working'
  message.value = ''
  try {
    const member = await acceptInvite(inviteToken.value)
    if (member.org_id) {
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

function continueToOrg() {
  void navigateTo('/org')
}
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-lg flex-col justify-center space-y-6 px-6 py-16 animate-fade-up">
    <div class="flex justify-center">
      <BrandLogo size="lg" />
    </div>
    <h1 class="text-3xl font-semibold tracking-tight">{{ t('invite.title') }}</h1>
    <p class="text-sm text-[var(--lp-muted)]">{{ t('invite.blurb') }}</p>

    <template v-if="!token">
      <p class="text-sm text-[var(--lp-muted)]">{{ t('invite.needAccount') }}</p>
      <div class="flex flex-col gap-3 sm:flex-row">
        <NuxtLink :to="loginRegisterUrl" class="lp-btn-primary text-center">
          {{ t('invite.registerToAccept') }}
        </NuxtLink>
        <NuxtLink :to="loginUrl" class="lp-btn-ghost text-center">
          {{ t('invite.loginToAccept') }}
        </NuxtLink>
      </div>
    </template>

    <template v-else>
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
        v-if="status !== 'ok'"
        type="button"
        class="lp-btn-primary w-full"
        :disabled="status === 'working'"
        @click="accept"
      >
        {{ status === 'working' ? t('invite.working') : t('invite.acceptOrg') }}
      </button>
      <button
        v-else
        type="button"
        class="lp-btn-primary w-full"
        @click="continueToOrg"
      >
        {{ t('invite.continueOrg') }}
      </button>
    </template>
  </div>
</template>
