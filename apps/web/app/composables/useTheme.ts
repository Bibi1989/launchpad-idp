export type LpTheme = 'dark' | 'light'

const THEME_KEY = 'lp_theme'

export function useTheme() {
  const theme = useState<LpTheme>('lp-theme', () => 'dark')

  function applyTheme(next: LpTheme) {
    theme.value = next
    if (!import.meta.client) return
    document.documentElement.dataset.theme = next
    document.documentElement.classList.toggle('dark', next === 'dark')
    document.documentElement.classList.toggle('light', next === 'light')
    window.localStorage.setItem(THEME_KEY, next)
    const meta = document.querySelector('meta[name="theme-color"]')
    if (meta) {
      meta.setAttribute('content', next === 'light' ? '#ffffff' : '#0c1219')
    }
  }

  function toggleTheme() {
    applyTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  function initTheme() {
    if (!import.meta.client) return
    const stored = window.localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') {
      applyTheme(stored)
      return
    }
    applyTheme('dark')
  }

  return {
    theme: readonly(theme),
    applyTheme,
    toggleTheme,
    initTheme,
  }
}
