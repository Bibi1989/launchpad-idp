import { resolvePreferredLocale, type LpLocale } from '~/composables/useLocalePreference'

export default defineNuxtPlugin((nuxtApp) => {
  const { initTheme } = useTheme()

  if (!import.meta.client) return

  initTheme()

  // Avoid useI18n() here - it requires component setup. Use the installed instance.
  const i18n = nuxtApp.$i18n
  const stored = window.localStorage.getItem('lp_locale')
  const next: LpLocale = resolvePreferredLocale(stored, navigator.language)
  if (i18n.locale.value !== next) {
    void i18n.setLocale(next)
  }
})
