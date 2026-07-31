export default defineNuxtRouteMiddleware((to) => {
  if (!import.meta.client) {
    return
  }

  const { token, ready } = useAuth()
  if (!ready.value) {
    return
  }

  const isPublic
    = to.path === '/'
      || to.path === '/login'
      || to.path === '/docs'
      || to.path.startsWith('/docs/')
      || to.path.startsWith('/auth/')
      || to.path.startsWith('/invite/')

  if (!token.value && !isPublic) {
    return navigateTo(`/login?next=${encodeURIComponent(to.fullPath)}`)
  }

  if (token.value && to.path === '/login') {
    const next = typeof to.query.next === 'string' ? to.query.next : '/home'
    return navigateTo(next.startsWith('/') ? next : '/home')
  }
})
