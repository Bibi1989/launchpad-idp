export default defineNuxtRouteMiddleware((to) => {
  if (!import.meta.client) {
    return
  }

  const { token, ready } = useAuth()
  if (!ready.value) {
    return
  }

  const isOnboarding = to.path === '/onboarding/org' || to.path.startsWith('/onboarding/')
  const isInvite = to.path.startsWith('/invite/')
  const isPublic
    = to.path === '/'
      || to.path === '/login'
      || to.path === '/docs'
      || to.path.startsWith('/docs/')
      || to.path.startsWith('/auth/')
      || isInvite

  if (!token.value && !isPublic) {
    return navigateTo(`/login?next=${encodeURIComponent(to.fullPath)}`)
  }

  if (token.value && to.path === '/login') {
    const next = typeof to.query.next === 'string' ? to.query.next : null
    if (next?.startsWith('/invite/')) {
      return navigateTo(next)
    }
    const { orgs } = useOrgs()
    if (orgs.value.length === 0) {
      return navigateTo('/onboarding/org')
    }
    return navigateTo(next && next.startsWith('/') ? next : '/home')
  }

  if (token.value && !isPublic) {
    const { orgs } = useOrgs()
    if (orgs.value.length === 0 && !isOnboarding && !isInvite) {
      return navigateTo('/onboarding/org')
    }
    if (orgs.value.length > 0 && isOnboarding) {
      return navigateTo('/home')
    }
  }
})
