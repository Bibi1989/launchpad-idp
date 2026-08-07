<script setup lang="ts">
import type { LpLocale } from '~/composables/useLocalePreference'

withDefaults(
  defineProps<{
    compact?: boolean
  }>(),
  { compact: false },
)

const { t } = useI18n()
const { theme, toggleTheme } = useTheme()
const { locale, setUserLocale } = useLocalePreference()

async function onLocaleChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value as LpLocale
  if (value === 'de' || value === 'en') {
    await setUserLocale(value)
  }
}
</script>

<template>
  <div
    class="flex items-center gap-2"
    :class="compact ? '' : 'flex-wrap'"
  >
    <button
      type="button"
      class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
      :aria-label="t('common.toggleTheme')"
      :title="theme === 'dark' ? t('common.themeLight') : t('common.themeDark')"
      @click="toggleTheme"
    >
      <span class="material-symbols-outlined text-[1.15rem]">
        {{ theme === 'dark' ? 'light_mode' : 'dark_mode' }}
      </span>
    </button>
    <label class="sr-only" for="lp-locale">{{ t('common.language') }}</label>
    <select
      id="lp-locale"
      class="lp-input h-9 w-auto py-1.5 text-xs"
      :class="compact ? 'min-w-[4.25rem] max-w-[5.5rem]' : 'min-w-[7.5rem]'"
      :value="locale"
      :aria-label="t('common.toggleLanguage')"
      @change="onLocaleChange"
    >
      <option value="de">{{ compact ? 'DE' : t('common.languageDe') }}</option>
      <option value="en">{{ compact ? 'EN' : t('common.languageEn') }}</option>
    </select>
  </div>
</template>
