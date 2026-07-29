<script setup lang="ts">
definePageMeta({ layout: false })

const route = useRoute()
const { token } = useAuth()
const { acceptInvite } = useOrgs()

const inviteToken = computed(() => String(route.params.token || ''))
const status = ref<'idle' | 'working' | 'ok' | 'error'>('idle')
const message = ref('')

async function accept() {
  if (!inviteToken.value || status.value === 'working') return
  status.value = 'working'
  message.value = ''
  try {
    const member = await acceptInvite(inviteToken.value)
    status.value = 'ok'
    message.value = `Joined as ${member.role}. Redirecting…`
    setTimeout(() => {
      void navigateTo('/')
    }, 1200)
  } catch (err) {
    status.value = 'error'
    message.value = err instanceof Error ? err.message : 'Could not accept invite'
  }
}

onMounted(() => {
  if (!import.meta.client) return
  if (!token.value) {
    const next = `/invite/${encodeURIComponent(inviteToken.value)}`
    void navigateTo(`/login?next=${encodeURIComponent(next)}`)
    return
  }
  void accept()
})
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-lg flex-col justify-center space-y-6 px-6 py-16 animate-fade-up">
    <h1 class="text-3xl font-semibold tracking-tight">Organization invite</h1>
    <p class="text-sm text-[var(--lp-muted)]">
      Accepting invitation token
      <span class="font-mono text-xs">{{ inviteToken.slice(0, 12) }}…</span>
    </p>
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
    <p v-else class="text-sm text-[var(--lp-muted)]">Working…</p>
  </div>
</template>
