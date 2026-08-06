<script setup lang="ts">
import type { TokenResponse } from '~/types/auth'

definePageMeta({
  layout: false,
})

const route = useRoute()
const config = useRuntimeConfig()
const { token } = useAuth()
const error = ref<string | null>(null)
const completing = ref(true)

onMounted(async () => {
  const code = typeof route.query.code === 'string' ? route.query.code : null
  const state = typeof route.query.state === 'string' ? route.query.state : null
  const expected = import.meta.client ? sessionStorage.getItem('launchpad_oidc_state') : null
  if (!code || !state) {
    error.value = 'Missing OIDC code or state'
    completing.value = false
    return
  }
  if (expected && expected !== state) {
    error.value = 'OIDC state mismatch - restart login'
    completing.value = false
    return
  }
  try {
    const response = await fetch(`${config.public.apiBase}/auth/oidc/callback`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ code, state }),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(body?.error?.message ?? 'OIDC callback failed')
    }
    const payload = (await response.json()) as TokenResponse
    if (import.meta.client) {
      localStorage.setItem('launchpad_access_token', payload.access_token)
      sessionStorage.removeItem('launchpad_oidc_state')
    }
    token.value = payload.access_token
    const { applyFromTokenResponse } = useOrgs()
    applyFromTokenResponse(payload)
    await navigateTo('/home')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'OIDC callback failed'
  } finally {
    completing.value = false
  }
})
</script>

<template>
  <div class="flex min-h-screen items-center justify-center px-6">
    <div class="lp-glass max-w-md space-y-3 rounded-2xl p-6 text-center">
      <p class="text-lg font-semibold">Completing sign-in…</p>
      <p v-if="completing" class="text-sm text-[var(--lp-muted)]">Exchanging OIDC authorization code</p>
      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
      <NuxtLink v-if="error" to="/login" class="text-sm text-[var(--lp-accent)] hover:underline">
        Back to login
      </NuxtLink>
    </div>
  </div>
</template>