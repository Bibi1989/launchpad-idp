<script setup lang="ts">
import type { Environment } from '~/types/environment'
import type { EnvironmentHealthPing, EnvironmentMetrics } from '~/types/observability'

const { t } = useI18n()
const route = useRoute()
const environmentId = computed(() => String(route.params.id || ''))
const { getById } = useEnvironments()
const { fetchMetrics, pingHealth } = useEnvironmentObservability()
const { orgs, activeOrgId } = useOrgs()

const environment = ref<Environment | null>(null)
const metrics = ref<EnvironmentMetrics | null>(null)
const health = ref<EnvironmentHealthPing | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const autoRefresh = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const orgName = computed(() => {
  const id = activeOrgId.value
  if (!id) return '-'
  return orgs.value.find((o) => o.id === id)?.name || '-'
})

const statusTone = computed(() => {
  if (health.value?.ok) return 'var(--lp-ok)'
  if (environment.value?.status === 'RUNNING') return 'var(--lp-warn)'
  return 'var(--lp-danger)'
})

const errorRateLabel = computed(() => {
  if (!health.value) return '-'
  return health.value.ok ? '0.00%' : '100%'
})

const latencyLabel = computed(() => {
  if (health.value?.latency_ms == null) return '-'
  return `${Math.round(health.value.latency_ms)}ms`
})

async function refresh() {
  if (!environmentId.value) return
  try {
    const [env, m, h] = await Promise.all([
      getById(environmentId.value),
      fetchMetrics(environmentId.value),
      pingHealth(environmentId.value),
    ])
    environment.value = env
    metrics.value = m
    health.value = h
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('envObservability.loadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => {
    if (autoRefresh.value) void refresh()
  }, 15000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="space-y-6">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <NuxtLink
          :to="`/environments/${environmentId}`"
          class="mb-2 inline-flex items-center gap-1 text-sm text-[var(--lp-muted)] hover:text-[var(--lp-fg)]"
        >
          <span class="material-symbols-outlined text-base">arrow_back</span>
          {{ t('envObservability.back') }}
        </NuxtLink>
        <div class="flex items-center gap-2">
          <span
            class="inline-block h-2.5 w-2.5 rounded-full"
            :style="{ background: statusTone, boxShadow: `0 0 10px ${statusTone}` }"
            aria-hidden="true"
          />
          <h1 class="text-2xl font-semibold tracking-tight">
            {{ t('envObservability.title') }}
          </h1>
        </div>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          {{
            t('envObservability.blurb', {
              name: environment?.name || environmentId,
            })
          }}
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="lp-btn-ghost pointer-events-none text-sm opacity-80">
          <span class="material-symbols-outlined text-base">calendar_today</span>
          {{ t('envObservability.window') }}
        </span>
        <button
          type="button"
          class="lp-btn-ghost text-sm"
          :class="autoRefresh ? 'border-[var(--lp-accent)] text-[var(--lp-accent)]' : ''"
          @click="autoRefresh = !autoRefresh"
        >
          <span class="material-symbols-outlined text-base">autorenew</span>
          {{ t('envObservability.autoRefresh') }}
        </button>
        <button type="button" class="lp-btn-ghost text-sm" :disabled="loading" @click="refresh">
          <span class="material-symbols-outlined text-base">refresh</span>
          {{ t('common.refresh') }}
        </button>
      </div>
    </header>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>

    <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div class="space-y-4">
        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="lp-label">{{ t('envObservability.cards.cpu') }}</p>
            <p class="mt-2 font-mono text-3xl">
              {{ loading ? '-' : (metrics?.cpu_cores ?? 0).toFixed(2) }}
            </p>
            <p class="mt-1 text-xs text-[var(--lp-muted)]">
              {{
                metrics?.cpu_percent != null
                  ? t('envObservability.cards.cpuHint', { pct: Math.round(metrics.cpu_percent) })
                  : t('envObservability.cards.sampleHint')
              }}
            </p>
          </div>
          <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="lp-label">{{ t('envObservability.cards.errorRate') }}</p>
            <p
              class="mt-2 font-mono text-3xl"
              :class="health?.ok === false ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-ok)]'"
            >
              {{ loading ? '-' : errorRateLabel }}
            </p>
            <p class="mt-1 text-xs text-[var(--lp-muted)]">
              {{ t('envObservability.cards.errorHint') }}
            </p>
          </div>
          <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="lp-label">{{ t('envObservability.cards.latency') }}</p>
            <p class="mt-2 font-mono text-3xl text-[var(--lp-accent)]">
              {{ loading ? '-' : latencyLabel }}
            </p>
            <p class="mt-1 text-xs text-[var(--lp-muted)]">
              {{ t('envObservability.cards.latencyHint') }}
            </p>
          </div>
          <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="lp-label">{{ t('envObservability.cards.alerts') }}</p>
            <p class="mt-2 font-mono text-3xl">
              {{ loading ? '-' : health?.ok === false ? 1 : 0 }}
            </p>
            <p
              v-if="health && !health.ok"
              class="mt-1 flex items-start gap-1 text-xs text-[var(--lp-warn)]"
            >
              <span class="material-symbols-outlined text-sm">warning</span>
              {{ health.message || t('envObservability.cards.previewDown') }}
            </p>
            <p v-else class="mt-1 text-xs text-[var(--lp-muted)]">
              {{ t('envObservability.cards.noAlerts') }}
            </p>
          </div>
        </div>

        <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
          <div class="mb-3 flex items-center justify-between gap-2">
            <h2 class="text-sm font-semibold">{{ t('envObservability.signals.title') }}</h2>
            <span class="text-xs text-[var(--lp-muted)]">{{ environment?.provider || 'local' }}</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full min-w-[520px] text-left text-sm">
              <thead class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">
                <tr>
                  <th class="pb-2 font-medium">{{ t('envObservability.signals.service') }}</th>
                  <th class="pb-2 font-medium">{{ t('envObservability.signals.status') }}</th>
                  <th class="pb-2 font-medium">{{ t('envObservability.signals.cpu') }}</th>
                  <th class="pb-2 font-medium">{{ t('envObservability.signals.memory') }}</th>
                  <th class="pb-2 font-medium">{{ t('envObservability.signals.health') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-t border-[var(--lp-line)]">
                  <td class="py-3 font-medium">{{ environment?.name || '-' }}</td>
                  <td class="py-3">
                    <span class="inline-flex items-center gap-1.5">
                      <span
                        class="h-2 w-2 rounded-full"
                        :style="{ background: statusTone }"
                      />
                      {{ environment?.status || '-' }}
                    </span>
                  </td>
                  <td class="py-3 font-mono">
                    {{ metrics?.available ? `${metrics.cpu_cores.toFixed(2)} cores` : '-' }}
                  </td>
                  <td class="py-3 font-mono">
                    {{ metrics?.available ? `${metrics.memory_gib.toFixed(2)} GiB` : '-' }}
                  </td>
                  <td class="py-3">
                    <span :class="health?.ok ? 'text-[var(--lp-ok)]' : 'text-[var(--lp-danger)]'">
                      {{
                        health
                          ? health.ok
                            ? t('envConsole.healthy')
                            : t('envConsole.unhealthy')
                          : '-'
                      }}
                    </span>
                  </td>
                </tr>
                <tr
                  v-if="environment?.enable_postgres"
                  class="border-t border-[var(--lp-line)]"
                >
                  <td class="py-3">postgres</td>
                  <td class="py-3">{{ environment.postgres_status || '-' }}</td>
                  <td class="py-3 font-mono">-</td>
                  <td class="py-3 font-mono">-</td>
                  <td class="py-3">{{ environment.postgres_status || '-' }}</td>
                </tr>
                <tr
                  v-if="environment?.enable_redis"
                  class="border-t border-[var(--lp-line)]"
                >
                  <td class="py-3">redis</td>
                  <td class="py-3">{{ environment.redis_status || '-' }}</td>
                  <td class="py-3 font-mono">-</td>
                  <td class="py-3 font-mono">-</td>
                  <td class="py-3">{{ environment.redis_status || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
          <h2 class="mb-3 text-sm font-semibold">{{ t('envObservability.trace.title') }}</h2>
          <div class="space-y-2 font-mono text-xs text-[var(--lp-muted)]">
            <div class="flex items-center gap-3">
              <span class="w-36 shrink-0">preview-health</span>
              <div class="h-2 flex-1 overflow-hidden rounded bg-[var(--lp-line)]">
                <div
                  class="h-full rounded"
                  :style="{
                    width: health?.latency_ms != null
                      ? `${Math.min(100, (health.latency_ms / 1000) * 100)}%`
                      : '8%',
                    background: health?.ok ? 'var(--lp-accent)' : 'var(--lp-danger)',
                  }"
                />
              </div>
              <span class="w-16 text-right">{{ latencyLabel }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="w-36 shrink-0">cpu-sample</span>
              <div class="h-2 flex-1 overflow-hidden rounded bg-[var(--lp-line)]">
                <div
                  class="h-full rounded bg-[var(--lp-ok)]"
                  :style="{ width: `${Math.max(4, Math.min(100, metrics?.cpu_percent ?? 4))}%` }"
                />
              </div>
              <span class="w-16 text-right">
                {{ metrics?.cpu_percent != null ? `${Math.round(metrics.cpu_percent)}%` : '-' }}
              </span>
            </div>
            <div class="flex items-center gap-3">
              <span class="w-36 shrink-0">memory-sample</span>
              <div class="h-2 flex-1 overflow-hidden rounded bg-[var(--lp-line)]">
                <div
                  class="h-full rounded bg-[var(--lp-warn)]"
                  :style="{ width: `${Math.max(4, Math.min(100, metrics?.memory_percent ?? 4))}%` }"
                />
              </div>
              <span class="w-16 text-right">
                {{ metrics?.memory_percent != null ? `${Math.round(metrics.memory_percent)}%` : '-' }}
              </span>
            </div>
          </div>
        </section>
      </div>

      <aside class="space-y-4">
        <section class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[#0b1219]">
          <div class="flex items-center justify-between border-b border-[var(--lp-line)] px-3 py-2">
            <div class="flex items-center gap-1.5">
              <span class="h-2.5 w-2.5 rounded-full bg-[var(--lp-danger)]" />
              <span class="h-2.5 w-2.5 rounded-full bg-[var(--lp-warn)]" />
              <span class="h-2.5 w-2.5 rounded-full bg-[var(--lp-ok)]" />
            </div>
            <span class="text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
              {{ t('envObservability.stream.title') }}
            </span>
          </div>
          <div class="max-h-72 space-y-1.5 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed">
            <p>
              <span class="text-[var(--lp-ok)]">[INFO]</span>
              {{ environment?.name || 'env' }}: status={{ environment?.status || '-' }}
            </p>
            <p>
              <span :class="health?.ok ? 'text-[var(--lp-ok)]' : 'text-[var(--lp-danger)]'">
                {{ health?.ok ? '[INFO]' : '[ERROR]' }}
              </span>
              health: {{ health?.message || '-' }}
            </p>
            <p>
              <span class="text-[var(--lp-ok)]">[INFO]</span>
              preview: {{ environment?.preview_url || '-' }}
            </p>
            <p>
              <span class="text-[var(--lp-muted)]">[INFO]</span>
              metrics source: {{ metrics?.source || '-' }}
            </p>
            <p v-if="metrics?.detail" class="text-[var(--lp-warn)]">
              [WARN] {{ metrics.detail }}
            </p>
          </div>
        </section>

        <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
          <p class="lp-label">{{ t('envObservability.context.title') }}</p>
          <dl class="mt-3 space-y-2 text-sm">
            <div class="flex justify-between gap-3">
              <dt class="text-[var(--lp-muted)]">{{ t('envObservability.context.target') }}</dt>
              <dd class="rounded bg-[var(--lp-line)]/40 px-2 py-0.5 font-mono text-xs">
                {{ environment?.namespace_name || '-' }}
              </dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-[var(--lp-muted)]">{{ t('envObservability.context.owner') }}</dt>
              <dd class="font-medium">{{ orgName }}</dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-[var(--lp-muted)]">{{ t('envObservability.context.provider') }}</dt>
              <dd>{{ environment?.provider || 'local' }}</dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-[var(--lp-muted)]">{{ t('envObservability.context.deploy') }}</dt>
              <dd>{{ environment?.deploy_mode || '-' }}</dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-[var(--lp-muted)]">{{ t('envObservability.context.appReady') }}</dt>
              <dd :class="environment?.app_ready ? 'text-[var(--lp-ok)]' : 'text-[var(--lp-warn)]'">
                {{ environment?.app_ready ? t('common.yes') : t('common.no') }}
              </dd>
            </div>
          </dl>
        </section>
      </aside>
    </div>
  </div>
</template>
