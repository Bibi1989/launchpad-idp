<script setup lang="ts">
import type { Environment } from '~/types/environment'

const route = useRoute()
const id = computed(() => String(route.params.id))
const { getById } = useEnvironments()

definePageMeta({
  layout: false,
})

const environment = ref<Environment | null>(null)
const loadError = ref<string | null>(null)
const tick = ref(0)

const remainingLabel = computed(() => {
  tick.value
  if (!environment.value) return '—'
  const left = environment.value.time_remaining_seconds
  if (left <= 0) return 'Expired'
  const hours = Math.floor(left / 3600)
  const minutes = Math.floor((left % 3600) / 60)
  return `${hours}h ${minutes}m`
})

const isLive = computed(() => environment.value?.status === 'RUNNING')

async function load() {
  try {
    environment.value = await getById(id.value)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Preview not found'
  }
}

onMounted(async () => {
  await load()
  const timer = setInterval(() => {
    tick.value += 1
    if (environment.value?.status === 'PROVISIONING') {
      void load()
    }
  }, 4000)
  onUnmounted(() => clearInterval(timer))
})
</script>

<template>
  <div class="min-h-screen bg-[#0b1219] text-[#e8eef5]">
    <div class="mx-auto flex min-h-screen max-w-4xl flex-col px-6 py-10">
      <header class="mb-10 flex items-center justify-between gap-4">
        <NuxtLink to="/" class="font-semibold tracking-tight text-[var(--lp-accent,#2dd4bf)]">
          Launchpad
        </NuxtLink>
        <NuxtLink
          v-if="environment"
          :to="`/environments/${environment.id}`"
          class="text-sm text-[#8fa3b8] hover:text-white"
        >
          Environment details →
        </NuxtLink>
      </header>

      <p v-if="loadError" class="text-[#f87171]">{{ loadError }}</p>

      <template v-else-if="environment">
        <div class="mb-8 space-y-3">
          <p class="font-mono text-xs uppercase tracking-[0.2em] text-[#2dd4bf]">Live preview</p>
          <h1 class="text-4xl font-semibold tracking-tight">{{ environment.name }}</h1>
          <p class="text-[#8fa3b8]">
            {{ environment.template_id || 'custom' }} · {{ environment.git_branch }}
          </p>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div class="rounded-xl border border-[#273447] bg-[#121a24] p-4">
            <p class="text-xs uppercase tracking-wide text-[#8fa3b8]">Status</p>
            <p class="mt-2 text-lg font-semibold">{{ environment.status }}</p>
          </div>
          <div class="rounded-xl border border-[#273447] bg-[#121a24] p-4">
            <p class="text-xs uppercase tracking-wide text-[#8fa3b8]">Time left</p>
            <p class="mt-2 font-mono text-lg">{{ remainingLabel }}</p>
          </div>
          <div class="rounded-xl border border-[#273447] bg-[#121a24] p-4">
            <p class="text-xs uppercase tracking-wide text-[#8fa3b8]">Cost to date</p>
            <p class="mt-2 font-mono text-lg text-[#2dd4bf]">${{ environment.cost_accrued }}</p>
          </div>
          <div class="rounded-xl border border-[#273447] bg-[#121a24] p-4">
            <p class="text-xs uppercase tracking-wide text-[#8fa3b8]">Rate</p>
            <p class="mt-2 font-mono text-lg">${{ environment.cost_estimate_hourly }}/hr</p>
          </div>
        </div>

        <div
          class="mt-8 flex flex-1 flex-col items-center justify-center rounded-2xl border border-dashed border-[#273447] bg-[#121a24]/80 px-6 py-16 text-center"
        >
          <template v-if="isLive">
            <span class="material-symbols-outlined mb-4 text-5xl text-[#2dd4bf]">rocket_launch</span>
            <h2 class="text-2xl font-semibold">Your preview is live</h2>
            <p class="mt-2 max-w-md text-sm text-[#8fa3b8]">
              This is the shareable status page. Use
              <strong class="font-medium text-[#e8eef5]">Open app</strong>
              for the running workload URL.
            </p>
            <div class="mt-6 flex flex-wrap justify-center gap-3">
              <a
                v-if="environment.app_ready && environment.preview_url"
                :href="environment.preview_url"
                class="inline-flex items-center gap-2 rounded-lg bg-[#2dd4bf] px-4 py-2 text-sm font-semibold text-[#0b1219]"
              >
                Open app
              </a>
              <NuxtLink
                :to="`/environments/${environment.id}`"
                class="inline-flex items-center gap-2 rounded-lg border border-[#273447] px-4 py-2 text-sm"
              >
                View logs
              </NuxtLink>
            </div>
          </template>
          <template v-else-if="environment.status === 'PROVISIONING'">
            <span class="material-symbols-outlined mb-4 animate-pulse text-5xl text-[#f59e0b]">hourglass_top</span>
            <h2 class="text-2xl font-semibold">Provisioning…</h2>
            <p class="mt-2 text-sm text-[#8fa3b8]">Hang tight — this page refreshes automatically.</p>
          </template>
          <template v-else>
            <span class="material-symbols-outlined mb-4 text-5xl text-[#8fa3b8]">cloud_off</span>
            <h2 class="text-2xl font-semibold">Preview unavailable</h2>
            <p class="mt-2 text-sm text-[#8fa3b8]">
              Status is {{ environment.status }}.
              <span v-if="environment.error_message">{{ environment.error_message }}</span>
            </p>
          </template>
        </div>
      </template>

      <p v-else class="text-[#8fa3b8]">Loading preview…</p>
    </div>
  </div>
</template>
