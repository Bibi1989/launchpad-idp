import { ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { runRequest } from '../app/utils/asyncRequest'

function state() {
  return { loading: ref(false), error: ref<string | null>('stale') }
}

describe('runRequest', () => {
  it('toggles loading and clears error around a successful call', async () => {
    const s = state()
    const result = await runRequest(s, async () => {
      expect(s.loading.value).toBe(true)
      expect(s.error.value).toBeNull()
      return 42
    })
    expect(result).toBe(42)
    expect(s.loading.value).toBe(false)
    expect(s.error.value).toBeNull()
  })

  it('records the error message and rethrows on failure', async () => {
    const s = state()
    await expect(
      runRequest(s, async () => {
        throw new Error('boom')
      }),
    ).rejects.toThrow('boom')
    expect(s.loading.value).toBe(false)
    expect(s.error.value).toBe('boom')
  })

  it('falls back to the provided message for non-Error throws', async () => {
    const s = state()
    await expect(
      // eslint-disable-next-line @typescript-eslint/no-throw-literal
      runRequest(s, async () => { throw 'nope' }, 'Custom failed'),
    ).rejects.toBe('nope')
    expect(s.error.value).toBe('Custom failed')
  })
})
