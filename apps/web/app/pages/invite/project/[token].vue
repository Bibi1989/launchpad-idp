<script setup lang="ts">
definePageMeta({ layout: false })

const { t } = useI18n()
const route = useRoute()
const { token, refreshMe } = useAuth()
const { setActiveOrg } = useOrgs()
const { acceptInvite } = useProjects()

const inviteToken = computed(() => String(route.params.token || ''))
const status = ref<'ready' | 'working' | 'ok' | 'error'>('ready')
const message = ref('')
const projectId = ref<string | null>(null)

const loginRegisterUrl = computed(() => {
  const next = `/invite/project/${encodeURIComponent(inviteToken.value)}`
  return `/login?mode=register&next=${encodeURIComponent(next)}`
})

const loginUrl = computed(() => {
  const next = `/invite/project/${encodeURIComponent(inviteToken.value)}`
  return `/login?mode=login&next=${encodeURIComponent(next)}`
})

async function accept() {
  if (!inviteToken.value || status.value === 'working') return
  status.value = 'working'
  message.value = ''
  try {
    const result = await acceptInvite(inviteToken.value)
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

function continueToProject() {
  if (projectId.value) {
    void navigateTo(`/projects/${projectId.value}`)
    return
  }
  void navigateTo('/projects')
}
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-lg flex-col justify-center space-y-6 px-6 py-16 animate-fade-up">
    <div class="flex justify-center">
      <BrandLogo size="lg" />
    </div>
    <h1 class="text-3xl font-semibold tracking-tight">{{ t('invite.projectTitle') }}</h1>
    <p class="text-sm text-[var(--lp-muted)]">{{ t('invite.projectBlurb') }}</p>

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
        {{ status === 'working' ? t('invite.working') : t('invite.acceptProject') }}
      </button>
      <button
        v-else
        type="button"
        class="lp-btn-primary w-full"
        @click="continueToProject"
      >
        {{ t('invite.continueProject') }}
      </button>
    </template>
  </div>
</template>
