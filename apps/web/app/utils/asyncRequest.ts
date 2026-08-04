import type { Ref } from 'vue'

export interface RequestState {
  loading: Ref<boolean>
  error: Ref<string | null>
}

/**
 * Run an async read request while toggling shared loading/error refs. Collapses
 * the `loading=true` / `error=null` / try / `catch(set error; rethrow)` /
 * `finally(loading=false)` ceremony repeated across data composables
 * (useDockerfiles, usePreviewAnalyzer, ...).
 *
 * This is the read-path sibling of `useAsyncAction` (the write/mutation path):
 * the error is both surfaced on `state.error` AND rethrown, so callers can still
 * branch on failure or let it bubble.
 */
export async function runRequest<T>(
  state: RequestState,
  fn: () => Promise<T>,
  fallbackMessage = 'Request failed',
): Promise<T> {
  state.loading.value = true
  state.error.value = null
  try {
    return await fn()
  } catch (err) {
    state.error.value = err instanceof Error ? err.message : fallbackMessage
    throw err
  } finally {
    state.loading.value = false
  }
}
