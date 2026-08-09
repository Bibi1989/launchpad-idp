<script setup lang="ts">
import { loginSchema, registerSchema } from '~/utils/authValidation'

definePageMeta({
  layout: false,
})

const { t } = useI18n()
const { login, register, devLogin, startOidcLogin, authConfig, token } = useAuth()
const route = useRoute()
const mode = ref<'login' | 'register'>(
  route.query.mode === 'register' ? 'register' : 'login',
)
const submitting = ref(false)
const error = ref<string | null>(null)

function postAuthPath(needsOrgSetup?: boolean): string {
  const next = route.query.next
  // Invite links always win so register → accept UI works before org onboarding.
  if (
    typeof next === 'string'
    && next.startsWith('/invite/')
  ) {
    return next
  }
  if (needsOrgSetup) return '/onboarding/org'
  if (
    typeof next === 'string'
    && next.startsWith('/')
    && next !== '/'
    && !next.startsWith('/onboarding/')
  ) {
    return next
  }
  return '/home'
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
      const { orgs } = useOrgs()
      void navigateTo(postAuthPath(orgs.value.length === 0))
    }
  },
  { immediate: true },
)

async function onLogin() {
  error.value = null
  const parsed = loginSchema.safeParse(loginForm)
  if (!parsed.success) {
    error.value = parsed.error.issues[0]?.message ?? t('auth.invalidForm')
    return
  }
  submitting.value = true
  try {
    const body = await login(parsed.data)
    await navigateTo(postAuthPath(Boolean(body.needs_org_setup)))
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('auth.loginFailed')
  } finally {
    submitting.value = false
  }
}

async function onRegister() {
  error.value = null
  const parsed = registerSchema.safeParse(registerForm)
  if (!parsed.success) {
    error.value = parsed.error.issues[0]?.message ?? t('auth.invalidForm')
    return
  }
  submitting.value = true
  try {
    const body = await register(parsed.data)
    await navigateTo(postAuthPath(Boolean(body.needs_org_setup)))
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('auth.registerFailed')
  } finally {
    submitting.value = false
  }
}

async function onDevLogin() {
  error.value = null
  submitting.value = true
  try {
    const body = await devLogin()
    await navigateTo(postAuthPath(Boolean(body.needs_org_setup)))
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('auth.devLoginFailed')
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
    error.value = err instanceof Error ? err.message : t('auth.ssoFailed')
    submitting.value = false
  }
}
</script>

<template>
  <div class="relative flex min-h-screen items-center justify-center px-6">
    <div class="absolute right-6 top-6 z-10">
      <PreferenceControls compact />
    </div>
    <div class="w-full max-w-md space-y-8 animate-fade-up">
      <div class="space-y-3 text-center">
        <div class="flex justify-center">
          <BrandLogo size="lg" />
        </div>
        <p class="text-sm text-[var(--lp-muted)]">{{ t('auth.subtitle') }}</p>
      </div>

      <div class="lp-glass space-y-5 rounded-2xl p-6">
        <div class="flex gap-4 text-sm">
          <button
            type="button"
            class="transition"
            :class="mode === 'login' ? 'text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'"
            @click="mode = 'login'"
          >
            {{ t('auth.login') }}
          </button>
          <button
            type="button"
            class="transition"
            :class="mode === 'register' ? 'text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'"
            @click="mode = 'register'"
          >
            {{ t('auth.register') }}
          </button>
        </div>

        <form v-if="mode === 'login'" class="space-y-4" @submit.prevent="onLogin">
          <label class="block space-y-2">
            <span class="lp-label">{{ t('auth.email') }}</span>
            <input
              v-model="loginForm.email"
              type="email"
              autocomplete="username"
              class="lp-input"
              required
            >
          </label>
          <PasswordInput
            v-model="loginForm.password"
            :label="t('auth.password')"
            autocomplete="current-password"
            required
          />
          <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
          <button type="submit" class="lp-btn-primary w-full" :disabled="submitting">
            {{ t('auth.submitLogin') }}
          </button>
        </form>

        <form v-else class="space-y-4" @submit.prevent="onRegister">
          <label class="block space-y-2">
            <span class="lp-label">{{ t('auth.displayName') }}</span>
            <input
              v-model="registerForm.display_name"
              type="text"
              autocomplete="name"
              class="lp-input"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">{{ t('auth.email') }}</span>
            <input
              v-model="registerForm.email"
              type="email"
              autocomplete="username"
              class="lp-input"
              required
            >
          </label>
          <PasswordInput
            v-model="registerForm.password"
            :label="t('auth.password')"
            autocomplete="new-password"
            required
          />
          <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
          <button type="submit" class="lp-btn-primary w-full" :disabled="submitting">
            {{ t('auth.submitRegister') }}
          </button>
        </form>

        <div class="space-y-2 border-t border-[var(--lp-line)] pt-4">
          <button
            v-if="authConfig?.oidc_enabled"
            type="button"
            class="lp-btn-ghost w-full"
            :disabled="submitting"
            @click="onOidcLogin"
          >
            {{ t('auth.ssoLogin') }}{{ authConfig?.oidc_provider_name ? ` (${authConfig.oidc_provider_name})` : '' }}
          </button>
          <button
            v-if="authConfig?.dev_login_enabled"
            type="button"
            class="lp-btn-ghost w-full"
            :disabled="submitting"
            @click="onDevLogin"
          >
            {{ t('auth.devLogin') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
