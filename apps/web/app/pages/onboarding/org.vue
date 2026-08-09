<script setup lang="ts">
definePageMeta({ layout: false })

const { t } = useI18n()
const { token, user, refreshMe } = useAuth()
const { orgs, createOrg, setActiveOrg } = useOrgs()

const name = ref('')
const submitting = ref(false)
const error = ref<string | null>(null)

watch(
  user,
  (value) => {
    if (value && !name.value) {
      const base = (value.display_name || value.email.split('@')[0] || 'My').trim()
      name.value = `${base}'s organization`
    }
  },
  { immediate: true },
)

onMounted(() => {
  if (!import.meta.client) return
  if (!token.value) {
    void navigateTo('/login?next=/onboarding/org')
    return
  }
  if (orgs.value.length > 0) {
    void navigateTo('/home')
  }
})

async function onSubmit() {
  if (submitting.value || !name.value.trim()) return
  submitting.value = true
  error.value = null
  try {
    const org = await createOrg({ name: name.value.trim() })
    setActiveOrg(org.id)
    await refreshMe()
    await navigateTo('/home')
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('onboarding.orgFailed')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="relative flex min-h-screen items-center justify-center px-6">
    <div class="w-full max-w-md space-y-8 animate-fade-up">
      <div class="space-y-3 text-center">
        <div class="flex justify-center">
          <BrandLogo size="lg" />
        </div>
        <h1 class="text-2xl font-semibold tracking-tight">{{ t('onboarding.orgTitle') }}</h1>
        <p class="text-sm text-[var(--lp-muted)]">{{ t('onboarding.orgBlurb') }}</p>
      </div>

      <form class="lp-glass space-y-5 rounded-2xl p-6" @submit.prevent="onSubmit">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('onboarding.orgName') }}</span>
          <input
            v-model="name"
            type="text"
            class="lp-input"
            required
            minlength="2"
            autocomplete="organization"
          >
        </label>
        <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
        <button type="submit" class="lp-btn-primary w-full" :disabled="submitting">
          {{ submitting ? t('common.loading') : t('onboarding.orgContinue') }}
        </button>
      </form>
    </div>
  </div>
</template>
