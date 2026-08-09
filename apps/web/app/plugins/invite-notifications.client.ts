/**
 * Sync pending org/project invites into the notification bell for the signed-in user.
 */
export default defineNuxtPlugin((nuxtApp) => {
  const { token, user } = useAuth()
  const { hydrate, upsert, removeWhere } = useNotifications()
  const { listPending } = usePendingInvites()

  hydrate()

  let timer: ReturnType<typeof setInterval> | null = null
  let inFlight = false

  function translate(key: string, params?: Record<string, unknown>): string {
    const i18n = nuxtApp.$i18n as { t: (k: string, p?: Record<string, unknown>) => string } | undefined
    if (i18n?.t) return i18n.t(key, params)
    return key
  }

  async function sync() {
    if (!import.meta.client || !token.value || !user.value || inFlight) return
    inFlight = true
    try {
      const pending = await listPending()
      const alive = new Set<string>()
      for (const invite of pending) {
        const id = `invite-${invite.kind}-${invite.invite_id}`
        alive.add(id)
        const title
          = invite.kind === 'org'
            ? translate('notifications.inviteOrgTitle', { org: invite.org_name })
            : translate('notifications.inviteProjectTitle', {
                project: invite.project_name || translate('nav.projects'),
              })
        const body
          = invite.kind === 'org'
            ? translate('notifications.inviteOrgBody', {
                role: invite.role,
                by: invite.invited_by || translate('notifications.someone'),
              })
            : translate('notifications.inviteProjectBody', {
                org: invite.org_name,
                role: invite.role,
                by: invite.invited_by || translate('notifications.someone'),
              })
        upsert({
          id,
          kind: 'invite',
          title,
          body,
          href: invite.href,
        })
      }
      removeWhere(
        (n) => n.kind === 'invite' && n.id.startsWith('invite-') && !alive.has(n.id),
      )
    } catch {
      // Auth may not be ready or API unavailable - retry on next tick.
    } finally {
      inFlight = false
    }
  }

  watch(
    [token, user],
    ([accessToken]) => {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
      if (!accessToken) {
        removeWhere((n) => n.kind === 'invite')
        return
      }
      void sync()
      timer = setInterval(() => {
        void sync()
      }, 45_000)
    },
    { immediate: true },
  )

  if (import.meta.client) {
    window.addEventListener('focus', () => {
      void sync()
    })
  }
})
