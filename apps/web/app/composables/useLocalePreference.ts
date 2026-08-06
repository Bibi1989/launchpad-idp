export type LpLocale = 'de' | 'en'

const LOCALE_KEY = 'lp_locale'

/** Resolve initial locale: user choice > browser language > German default. */
export function resolvePreferredLocale(stored: string | null, navigatorLang?: string): LpLocale {
  if (stored === 'de' || stored === 'en') return stored
  const lang = (navigatorLang || '').toLowerCase()
  if (lang.startsWith('en')) return 'en'
  if (lang.startsWith('de')) return 'de'
  return 'de'
}

export function useLocalePreference() {
  // Prefer `$i18n` so this works in Nuxt plugins (not only component setup).
  const { $i18n } = useNuxtApp()
  const locale = $i18n.locale
  const locales = $i18n.locales
  const setLocale = $i18n.setLocale.bind($i18n)

  async function setUserLocale(code: LpLocale) {
    if (import.meta.client) {
      window.localStorage.setItem(LOCALE_KEY, code)
    }
    await setLocale(code)
  }

  function clearUserLocale() {
    if (import.meta.client) {
      window.localStorage.removeItem(LOCALE_KEY)
    }
  }

  async function initFromSystemOrUser() {
    if (!import.meta.client) return
    const stored = window.localStorage.getItem(LOCALE_KEY)
    const next = resolvePreferredLocale(stored, navigator.language)
    if (locale.value !== next) {
      await setLocale(next)
    }
  }

  return {
    locale,
    locales,
    setUserLocale,
    clearUserLocale,
    initFromSystemOrUser,
  }
}
