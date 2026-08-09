export default defineNuxtPlugin(async () => {
  const { init } = useAuth()
  try {
    await init()
  } catch {
    // init() already marks ready in finally; never block app bootstrap.
  }
})
