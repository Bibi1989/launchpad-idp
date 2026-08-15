/**
 * Watches the shared environments state and turns lifecycle transitions
 * (ready / failed / TTL warning / cost cap) into in-app notifications + toasts.
 * The first snapshot is recorded silently so existing environments don't
 * generate a burst of alerts on page load.
 */
export default defineNuxtPlugin(() => {
  const { environments } = useEnvironments()
  const { reconcileMany, hydrate } = useNotifications()

  hydrate()

  watch(
    environments,
    (list) => {
      if (!list?.length) return
      try {
        reconcileMany(list)
      } catch (err) {
        console.error('[launchpad] environment notification reconcile failed', err)
      }
    },
    { deep: true, immediate: true },
  )
})
