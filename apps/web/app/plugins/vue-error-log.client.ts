/**
 * Log unhandled Vue errors with the production info code decoded.
 * Helps diagnose Nuxt full-screen 500 pages that only show a digit (e.g. "2").
 */
const VUE_RUNTIME_INFO: Record<string | number, string> = {
  0: 'setup function',
  1: 'render function',
  2: 'watcher getter',
  3: 'watcher callback',
  4: 'watcher cleanup function',
  5: 'native event handler',
  6: 'component event handler',
  7: 'vnode hook',
  8: 'directive hook',
  9: 'transition hook',
  10: 'app errorHandler',
  11: 'app warnHandler',
  12: 'ref function',
  13: 'async component loader',
  14: 'scheduler flush',
  15: 'component update',
  16: 'app unmount cleanup function',
}

export default defineNuxtPlugin((nuxtApp) => {
  nuxtApp.hook('vue:error', (error, _instance, info) => {
    const infoKey = info as string | number
    const label = VUE_RUNTIME_INFO[infoKey] ?? String(info ?? '')
    console.error('[launchpad] vue:error', {
      info: infoKey,
      infoLabel: label,
      message: error instanceof Error ? error.message : String(error),
      error,
    })
  })
})
