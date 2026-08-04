<script setup lang="ts">
import type { Environment } from '~/types/environment'

const route = useRoute()
const { refresh, environments } = useEnvironments()

const prNumber = computed(() => {
  const raw = Number(route.params.number)
  return Number.isFinite(raw) && raw > 0 ? raw : null
})

const loading = ref(true)
const errorMessage = ref<string | null>(null)

const matches = computed(() => {
  if (prNumber.value == null) return []
  return environments.value.filter((env) => env.github_pr_number === prNumber.value)
})

onMounted(async () => {
  loading.value = true
  try {
    await refresh()
    if (matches.value.length === 1) {
      const env = matches.value[0]!
      if (env.preview_url && env.app_ready) {
        await navigateTo(resolvePreviewUrl(env)!, { external: true })
        return
      }
      await navigateTo(`/environments/${env.id}`)
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to resolve PR preview'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="mx-auto max-w-2xl animate-fade-up space-y-6 pb-12">
    <header>
      <p class="lp-label mb-1">PR preview</p>
      <h1 class="text-2xl font-semibold">
        Stable URL for PR #{{ prNumber ?? '—' }}
      </h1>
      <p class="mt-2 text-sm text-[var(--lp-muted)]">
        This path is the Launchpad stable PR preview link. Closing the PR tears the environment down automatically.
      </p>
    </header>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Resolving preview…</p>
    <p v-else-if="errorMessage" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>
    <p v-else-if="!matches.length" class="rounded-xl border border-dashed border-[var(--lp-line)] p-6 text-sm text-[var(--lp-muted)]">
      No active preview linked to this PR. Launch one from
      <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">/launch</NuxtLink>
      with the PR number set.
    </p>
    <ul v-else class="space-y-3">
      <li
        v-for="env in matches"
        :key="env.id"
        class="rounded-xl border border-[var(--lp-line)] p-4"
      >
        <p class="font-semibold">{{ env.name }}</p>
        <p class="mt-1 font-mono text-xs text-[var(--lp-muted)]">{{ env.status }}</p>
        <div class="mt-3 flex flex-wrap gap-2">
          <NuxtLink :to="`/environments/${env.id}`" class="lp-btn-primary py-1.5 text-xs uppercase tracking-wide">
            Open in Launchpad
          </NuxtLink>
          <a
            v-if="env.preview_url"
            :href="resolvePreviewUrl(env)!"
            class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
            target="_blank"
            rel="noreferrer"
          >
            Open app
          </a>
        </div>
      </li>
    </ul>
  </div>
</template>
