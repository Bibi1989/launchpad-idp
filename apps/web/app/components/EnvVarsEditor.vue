<script setup lang="ts">
import type { EnvVarOverride } from '~/types/repoImport'
import { parseEnvBlock } from '~/utils/parseEnvBlock'

/**
 * Reusable environment-variable editor for a service (linked repo, imported repo,
 * or a plain service). Values flow to the backend as `env_vars` (EnvVarOverride[])
 * and are injected into the container. Users can add rows one by one, or paste a
 * whole `.env` block which is parsed into rows automatically.
 */
const model = defineModel<EnvVarOverride[]>({ required: true })

const { t } = useI18n()

const pasteOpen = ref(false)
const pasteText = ref('')

// Values are masked by default (they can be secrets); the eye icon reveals one row.
const revealed = ref<Set<number>>(new Set())
function toggleReveal(index: number) {
  const next = new Set(revealed.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  revealed.value = next
}

function addRow() {
  model.value = [...model.value, { key: '', value: '' }]
}

function removeRow(index: number) {
  model.value = model.value.filter((_, i) => i !== index)
}

function updateKey(index: number, key: string) {
  model.value = model.value.map((row, i) => (i === index ? { ...row, key } : row))
}

function updateValue(index: number, value: string) {
  model.value = model.value.map((row, i) => (i === index ? { ...row, value } : row))
}

/** Merge parsed pairs into the existing rows: existing keys are updated, new keys appended. */
function applyPaste() {
  const parsed = parseEnvBlock(pasteText.value)
  if (!parsed.length) {
    pasteOpen.value = false
    pasteText.value = ''
    return
  }
  const next = [...model.value]
  for (const { key, value } of parsed) {
    const idx = next.findIndex((r) => r.key === key)
    if (idx >= 0) next[idx] = { key, value }
    else next.push({ key, value })
  }
  // Drop leading empty placeholder rows so a fresh paste doesn't leave a blank line.
  model.value = next.filter((r) => r.key.trim() !== '' || r.value.trim() !== '')
  pasteText.value = ''
  pasteOpen.value = false
}

const parsedCount = computed(() => parseEnvBlock(pasteText.value).length)
</script>

<template>
  <div class="space-y-2">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="lp-label text-[11px]">{{ t('envVars.title') }}</span>
      <div class="flex items-center gap-2">
        <button type="button" class="lp-btn-ghost text-xs" @click="pasteOpen = !pasteOpen">
          <span class="material-symbols-outlined text-sm">content_paste</span>
          {{ t('envVars.paste') }}
        </button>
        <button type="button" class="lp-btn-ghost text-xs" @click="addRow">
          <span class="material-symbols-outlined text-sm">add</span>
          {{ t('envVars.add') }}
        </button>
      </div>
    </div>

    <div v-if="pasteOpen" class="space-y-2 rounded-lg border border-[var(--lp-line)] p-2">
      <p class="text-[11px] text-[var(--lp-muted)]">{{ t('envVars.pasteHint') }}</p>
      <textarea
        v-model="pasteText"
        rows="5"
        class="lp-input w-full font-mono text-xs"
        :placeholder="'DATABASE_URL=postgres://…\nAPI_KEY=sk-…\nFEATURE_FLAG=true'"
        spellcheck="false"
        autocomplete="off"
      />
      <div class="flex items-center justify-between gap-2">
        <span class="text-[11px] text-[var(--lp-muted)]">
          {{ t('envVars.pasteParsed', { count: parsedCount }) }}
        </span>
        <div class="flex items-center gap-2">
          <button type="button" class="lp-btn-ghost text-xs" @click="pasteOpen = false; pasteText = ''">
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            class="lp-btn-secondary text-xs"
            :disabled="parsedCount === 0"
            @click="applyPaste"
          >
            {{ t('envVars.pasteApply') }}
          </button>
        </div>
      </div>
    </div>

    <p v-if="!model.length" class="text-[11px] text-[var(--lp-muted)]">
      {{ t('envVars.empty') }}
    </p>

    <div v-for="(row, index) in model" :key="index" class="flex items-center gap-2">
      <input
        class="lp-input font-mono text-xs flex-1"
        :value="row.key"
        :placeholder="t('envVars.keyPlaceholder')"
        autocomplete="off"
        spellcheck="false"
        @input="updateKey(index, ($event.target as HTMLInputElement).value)"
      >
      <span class="text-[var(--lp-muted)]">=</span>
      <div class="relative flex-1">
        <input
          class="lp-input font-mono text-xs w-full pr-8"
          :type="revealed.has(index) ? 'text' : 'password'"
          :value="row.value"
          :placeholder="t('envVars.valuePlaceholder')"
          autocomplete="off"
          spellcheck="false"
          @input="updateValue(index, ($event.target as HTMLInputElement).value)"
        >
        <button
          type="button"
          class="absolute inset-y-0 right-1 flex items-center px-1 text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
          :aria-label="revealed.has(index) ? t('envVars.hide') : t('envVars.reveal')"
          @click="toggleReveal(index)"
        >
          <span class="material-symbols-outlined text-sm">
            {{ revealed.has(index) ? 'visibility_off' : 'visibility' }}
          </span>
        </button>
      </div>
      <button
        type="button"
        class="lp-btn-ghost text-xs"
        :aria-label="t('envVars.remove')"
        @click="removeRow(index)"
      >
        <span class="material-symbols-outlined text-sm">close</span>
      </button>
    </div>

    <p class="flex items-start gap-1.5 pt-1 text-[11px] text-[var(--lp-muted)]">
      <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">info</span>
      <span>{{ t('envVars.corsNote') }}</span>
    </p>
  </div>
</template>
