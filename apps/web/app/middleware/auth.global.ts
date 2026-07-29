export default defineNuxtRouteMiddleware((to) => {
  if (!import.meta.client) {
    return
  }

  const { token, ready } = useAuth()
  if (!ready.value) {
    return
  }

  const isPublicAuth
    = to.path === '/login'
      || to.path.startsWith('/auth/')
      || to.path.startsWith('/invite/')

  if (!token.value && !isPublicAuth) {
    const next = to.fullPath !== '/' ? `?next=${encodeURIComponent(to.fullPath)}` : ''
    return navigateTo(`/login${next}`)
  }

  if (token.value && to.path === '/login') {
    const next = typeof to.query.next === 'string' ? to.query.next : '/'
    return navigateTo(next.startsWith('/') ? next : '/')
  }
})
