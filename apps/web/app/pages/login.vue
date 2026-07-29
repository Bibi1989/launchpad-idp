<script setup lang="ts">
import { loginSchema, registerSchema } from '~/utils/authValidation'

definePageMeta({
  layout: false,
})

const { login, register, devLogin, startOidcLogin, authConfig, token } = useAuth()
const route = useRoute()
const mode = ref<'login' | 'register'>('login')
const submitting = ref(false)
const error = ref<string | null>(null)

function postAuthPath(): string {
  const next = route.query.next
  if (typeof next === 'string' && next.startsWith('/')) return next
  return '/'
}

const loginForm = reactive({
  email: '',
  password: '',
})

const registerForm = reactive({
  email: '',
  password: '',
  display_name: '',
})

watch(
  token,
  (value) => {
    if (value) {
      void navigateTo(postAuthPath())
    }
  },
  { immediate: true },
)

async function onLogin() {
  error.value = null
  const parsed = loginSchema.safeParse(loginForm)
  if (!parsed.success) {
    error.value = parsed.error.issues[0]?.message ?? 'Invalid form'
    return
  }
  submitting.value = true
  try {
    await login(parsed.data)
    await navigateTo(postAuthPath())
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Login failed'
  } finally {
    submitting.value = false
  }
}

async function onRegister() {
  error.value = null
  const parsed = registerSchema.safeParse(registerForm)
  if (!parsed.success) {
    error.value = parsed.error.issues[0]?.message ?? 'Invalid form'
    return
  }
  submitting.value = true
  try {
    await register(parsed.data)
    await navigateTo(postAuthPath())
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Registration failed'
  } finally {
    submitting.value = false
  }
}

async function onDevLogin() {
  error.value = null
  submitting.value = true
  try {
    await devLogin()
    await navigateTo(postAuthPath())
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Dev login failed'
  } finally {
    submitting.value = false
  }
}

async function onOidcLogin() {
  error.value = null
  submitting.value = true
  try {
    await startOidcLogin()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'SSO login failed'
    submitting.value = false
  }
}
</script>

<template>
  <div class="relative flex min-h-screen items-center justify-center px-6">
    <div class="w-full max-w-md space-y-8 animate-fade-up">
      <div class="space-y-2 text-center">
        <p class="text-3xl font-semibold tracking-tight text-[var(--lp-accent)]">Launchpad</p>
        <p class="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--lp-muted)]">IDP</p>
        <p class="text-sm text-[var(--lp-muted)]">Sign in to provision governed environments</p>
      </div>

      <div class="lp-glass space-y-5 rounded-2xl p-6">
        <div class="flex gap-4 text-sm">
          <button
            type="button"
            class="transition"
            :class="mode === 'login' ? 'text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'"
            @click="mode = 'login'"
          >
            Login
          </button>
          <button
            type="button"
            class="transition"
            :class="mode === 'register' ? 'text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'"
            @click="mode = 'register'"
          >
            Register
          </button>
        </div>

        <form v-if="mode === 'login'" class="space-y-4" @submit.prevent="onLogin">
          <label class="block space-y-2">
            <span class="lp-label">Email</span>
            <input
              v-model="loginForm.email"
              type="email"
              autocomplete="username"
              class="lp-input"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Password</span>
            <input
              v-model="loginForm.password"
              type="password"
              autocomplete="current-password"
              class="lp-input"
            >
          </label>
          <button type="submit" class="lp-btn-primary w-full" :disabled="submitting">
            {{ submitting ? 'Signing in…' : 'Sign in' }}
          </button>
        </form>

        <form v-else class="space-y-4" @submit.prevent="onRegister">
          <label class="block space-y-2">
            <span class="lp-label">Display name</span>
            <input v-model="registerForm.display_name" autocomplete="name" class="lp-input">
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Email</span>
            <input
              v-model="registerForm.email"
              type="email"
              autocomplete="username"
              class="lp-input"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Password</span>
            <input
              v-model="registerForm.password"
              type="password"
              autocomplete="new-password"
              class="lp-input"
            >
          </label>
          <button type="submit" class="lp-btn-primary w-full" :disabled="submitting">
            {{ submitting ? 'Creating…' : 'Create account' }}
          </button>
        </form>

        <button
          v-if="authConfig?.oidc_enabled"
          type="button"
          class="lp-btn-ghost w-full"
          :disabled="submitting"
          @click="onOidcLogin"
        >
          Continue with {{ authConfig.oidc_provider_name || 'SSO' }}
        </button>

        <button
          v-if="authConfig?.dev_login_enabled"
          type="button"
          class="lp-btn-ghost w-full"
          :disabled="submitting"
          @click="onDevLogin"
        >
          Dev login
        </button>

        <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
      </div>
    </div>
  </div>
</template>
