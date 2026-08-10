<script setup lang="ts">
import type {
  DatastoreImportConfig,
  DatastoreImportPlacement,
  EnvExampleVar,
  EnvVarOverride,
} from '~/types/repoImport'

const props = defineProps<{
  envExample: EnvExampleVar[]
  detectedDatastores: string[]
  suggestions: Record<string, { in_cluster?: string; external?: string }>
  runtimeMode: 'kubernetes' | 'docker_compose' | 'running_instance'
}>()

const envVars = defineModel<EnvVarOverride[]>('envVars', { required: true })
const datastoreConfigs = defineModel<DatastoreImportConfig[]>('datastoreConfigs', { required: true })

const { t } = useI18n()

const showSecrets = ref(false)

function ensureDatastoreRow(kind: string): DatastoreImportConfig {
  let row = datastoreConfigs.value.find((d) => d.kind === kind)
  if (!row) {
    row = {
      kind,
      placement: props.runtimeMode === 'kubernetes' ? 'in_cluster' : 'external',
      connection_url: props.suggestions[kind]?.external || '',
    }
    datastoreConfigs.value = [...datastoreConfigs.value, row]
  }
  return row
}

watch(
  () => [props.detectedDatastores, props.runtimeMode] as const,
  () => {
    for (const kind of props.detectedDatastores) {
      ensureDatastoreRow(kind)
    }
  },
  { immediate: true },
)

function setPlacement(kind: string, placement: DatastoreImportPlacement) {
  const row = ensureDatastoreRow(kind)
  row.placement = placement
  if (placement === 'in_cluster') {
    row.connection_url = props.suggestions[kind]?.in_cluster || ''
  } else if (placement === 'external' && !row.connection_url) {
    row.connection_url = props.suggestions[kind]?.external || ''
  }
  datastoreConfigs.value = datastoreConfigs.value.map((d) => (d.kind === kind ? { ...row } : d))
}

function setConnectionUrl(kind: string, url: string) {
  const row = ensureDatastoreRow(kind)
  row.connection_url = url
  datastoreConfigs.value = datastoreConfigs.value.map((d) => (d.kind === kind ? { ...row } : d))
}

function applySuggested(kind: string, which: 'in_cluster' | 'external') {
  const url = props.suggestions[kind]?.[which] || ''
  if (!url) return
  setConnectionUrl(kind, url)
}

function updateEnv(key: string, value: string) {
  const next = [...envVars.value]
  const idx = next.findIndex((e) => e.key === key)
  if (idx >= 0) {
    next[idx] = { key, value }
  } else {
    next.push({ key, value })
  }
  envVars.value = next
}

function envValue(key: string): string {
  return envVars.value.find((e) => e.key === key)?.value ?? ''
}

const visibleEnv = computed(() =>
  props.envExample.filter((item) => showSecrets.value || !item.is_secret),
)

const datastoreLabel: Record<string, string> = {
  postgres: 'PostgreSQL',
  mysql: 'MySQL',
  mariadb: 'MariaDB',
  mongodb: 'MongoDB',
  redis: 'Redis',
}
</script>

<template>
  <div class="space-y-4">
    <div
      v-if="detectedDatastores.length"
      class="space-y-3 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-surface)]/40 p-4"
    >
      <div>
        <h4 class="text-sm font-semibold">{{ t('import.datastoresTitle') }}</h4>
        <p class="mt-1 text-xs text-[var(--lp-muted)]">{{ t('import.datastoresBlurb') }}</p>
      </div>
      <div
        v-for="kind in detectedDatastores"
        :key="kind"
        class="space-y-2 rounded-lg border border-[var(--lp-line)]/80 p-3"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="text-sm font-medium">{{ datastoreLabel[kind] || kind }}</span>
          <select
            class="lp-input w-auto min-w-[10rem] text-xs"
            :value="ensureDatastoreRow(kind).placement"
            @change="setPlacement(kind, ($event.target as HTMLSelectElement).value as DatastoreImportPlacement)"
          >
            <option value="in_cluster">{{ t('import.datastoreInCluster') }}</option>
            <option value="external">{{ t('import.datastoreExternal') }}</option>
            <option value="skip">{{ t('import.datastoreSkip') }}</option>
          </select>
        </div>
        <p v-if="ensureDatastoreRow(kind).placement === 'in_cluster'" class="text-[11px] text-[var(--lp-muted)]">
          {{ t('import.datastoreInClusterHint') }}
          <button
            type="button"
            class="text-[var(--lp-accent)] hover:underline"
            @click="applySuggested(kind, 'in_cluster')"
          >
            {{ t('import.useSuggestedUrl') }}
          </button>
        </p>
        <template v-if="ensureDatastoreRow(kind).placement === 'external'">
          <label class="block space-y-1">
            <span class="lp-label text-[11px]">{{ t('import.connectionUrl') }}</span>
            <input
              class="lp-input font-mono text-xs"
              :value="ensureDatastoreRow(kind).connection_url || ''"
              :placeholder="suggestions[kind]?.external || 'postgresql://…'"
              autocomplete="off"
              @input="setConnectionUrl(kind, ($event.target as HTMLInputElement).value)"
            >
          </label>
          <p class="text-[11px] text-[var(--lp-muted)]">
            {{ t('import.datastoreExternalHint') }}
            <button
              type="button"
              class="text-[var(--lp-accent)] hover:underline"
              @click="applySuggested(kind, 'external')"
            >
              {{ t('import.useSuggestedUrl') }}
            </button>
          </p>
        </template>
      </div>
    </div>

    <div
      v-if="envExample.length"
      class="space-y-3 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-surface)]/40 p-4"
    >
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 class="text-sm font-semibold">{{ t('import.envTitle') }}</h4>
          <p class="mt-1 text-xs text-[var(--lp-muted)]">{{ t('import.envBlurb') }}</p>
        </div>
        <label class="flex items-center gap-2 text-xs text-[var(--lp-muted)] cursor-pointer">
          <input v-model="showSecrets" type="checkbox" class="accent-[var(--lp-accent)]">
          {{ t('import.showSecretKeys') }}
        </label>
      </div>
      <div class="max-h-64 space-y-2 overflow-y-auto pr-1">
        <label
          v-for="item in visibleEnv"
          :key="item.key"
          class="block space-y-1"
        >
          <span class="flex flex-wrap items-baseline gap-2">
            <span class="font-mono text-xs font-medium">{{ item.key }}</span>
            <span v-if="item.is_secret" class="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-200">
              {{ t('import.secretBadge') }}
            </span>
            <span v-if="item.comment" class="text-[10px] text-[var(--lp-muted)]">{{ item.comment }}</span>
          </span>
          <input
            class="lp-input font-mono text-xs"
            :type="item.is_secret && !showSecrets ? 'password' : 'text'"
            :value="envValue(item.key)"
            :placeholder="item.example_value || item.suggested_value || '…'"
            autocomplete="off"
            @input="updateEnv(item.key, ($event.target as HTMLInputElement).value)"
          >
          <span class="text-[10px] text-[var(--lp-muted)]">{{ item.source }}</span>
        </label>
      </div>
    </div>
    <p
      v-else-if="!detectedDatastores.length"
      class="text-xs text-[var(--lp-muted)]"
    >
      {{ t('import.noEnvExample') }}
    </p>
  </div>
</template>
