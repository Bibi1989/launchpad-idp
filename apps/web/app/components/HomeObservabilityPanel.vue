<script setup lang="ts">
import type { EnvironmentObservabilitySummary } from '~/types/observability'

const props = withDefaults(
  defineProps<{
    pollMs?: number
  }>(),
  { pollMs: 20000 },
)

const { t } = useI18n()
const { fetchSummary } = useEnvironmentObservability()

const summary = ref<EnvironmentObservabilitySummary | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  try {
    summary.value = await fetchSummary(12)
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('home.observability.loadFailed')
  } finally {
    loading.value = false
  }
}

function barWidth(pct: number | null | undefined): string {
  return `${Math.max(0, Math.min(100, pct ?? 0))}%`
}

function barColor(pct: number | null | undefined): string {
  const v = pct ?? 0
  if (v >= 85) return 'var(--lp-danger)'
  if (v >= 60) return 'var(--lp-warn)'
  return 'var(--lp-accent)'
}

function healthTone(ok: boolean, hasUrl: boolean): string {
  if (!hasUrl) return 'var(--lp-muted)'
  return ok ? 'var(--lp-ok)' : 'var(--lp-danger)'
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => {
    void refresh()
  }, Math.max(props.pollMs, 8000))
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold">{{ t('home.observability.title') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ t('home.observability.blurb') }}</p>
      </div>
      <button type="button" class="lp-btn-ghost text-sm" :disabled="loading" @click="refresh">
        <span class="material-symbols-outlined text-base">monitoring</span>
        {{ t('home.observability.refresh') }}
      </button>
    </div>

    <div class="grid gap-3 sm:grid-cols-3">
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
        <p class="lp-label">{{ t('home.observability.healthy') }}</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-ok)]">
          {{ loading ? '-' : summary?.healthy_count ?? 0 }}
        </p>
      </div>
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
        <p class="lp-label">{{ t('home.observability.unhealthy') }}</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-danger)]">
          {{ loading ? '-' : summary?.unhealthy_count ?? 0 }}
        </p>
      </div>
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
        <p class="lp-label">{{ t('home.observability.unknown') }}</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-muted)]">
          {{ loading ? '-' : summary?.unknown_count ?? 0 }}
        </p>
      </div>
    </div>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>

    <div
      v-if="!loading && summary && summary.items.length === 0"
      class="rounded-xl border border-dashed border-[var(--lp-line)] p-6 text-sm text-[var(--lp-muted)]"
    >
      {{ t('home.observability.empty') }}
    </div>

    <ul v-else class="space-y-3">
      <li
        v-for="item in summary?.items || []"
        :key="item.environment_id"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <NuxtLink
              :to="`/environments/${item.environment_id}`"
              class="font-medium text-[var(--lp-text)] hover:text-[var(--lp-accent)]"
            >
              {{ item.name }}
            </NuxtLink>
            <p class="mt-1 font-mono text-xs text-[var(--lp-muted)]">
              {{ item.status }}
              <span v-if="item.provider"> · {{ item.provider }}</span>
            </p>
          </div>
          <div class="flex items-center gap-2 text-sm">
            <span
              class="inline-flex items-center gap-1.5 rounded-full border border-[var(--lp-line)] px-2.5 py-1"
              :style="{ color: healthTone(item.health.ok, Boolean(item.health.preview_url)) }"
            >
              <span class="material-symbols-outlined text-base">
                {{ item.health.ok ? 'check_circle' : item.health.preview_url ? 'error' : 'help' }}
              </span>
              {{
                item.health.preview_url
                  ? item.health.ok
                    ? t('home.observability.pingOk')
                    : t('home.observability.pingFail')
                  : t('home.observability.pingNone')
              }}
              <span v-if="item.health.latency_ms != null" class="font-mono text-xs opacity-80">
                {{ item.health.latency_ms }}ms
              </span>
            </span>
          </div>
        </div>

        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <div class="mb-1 flex justify-between text-xs text-[var(--lp-muted)]">
              <span>{{ t('home.observability.cpu') }}</span>
              <span class="font-mono">
                {{ item.metrics.available ? `${item.metrics.cpu_cores.toFixed(2)} cores` : 'N/A' }}
              </span>
            </div>
            <div class="h-1.5 overflow-hidden rounded-full bg-[var(--lp-line)]">
              <div
                class="h-full rounded-full"
                :style="{
                  width: barWidth(item.metrics.cpu_percent),
                  background: barColor(item.metrics.cpu_percent),
                }"
              />
            </div>
          </div>
          <div>
            <div class="mb-1 flex justify-between text-xs text-[var(--lp-muted)]">
              <span>{{ t('home.observability.memory') }}</span>
              <span class="font-mono">
                {{ item.metrics.available ? `${item.metrics.memory_gib.toFixed(2)} GiB` : 'N/A' }}
              </span>
            </div>
            <div class="h-1.5 overflow-hidden rounded-full bg-[var(--lp-line)]">
              <div
                class="h-full rounded-full"
                :style="{
                  width: barWidth(item.metrics.memory_percent),
                  background: barColor(item.metrics.memory_percent),
                }"
              />
            </div>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
