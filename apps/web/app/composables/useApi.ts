import type { ApiErrorBody } from '~/types/environment'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly correlationId: string | null

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.error.message ?? fallback)
    this.name = 'ApiError'
    this.status = status
    this.code = body?.error.code ?? 'unknown_error'
    this.correlationId = body?.error.correlation_id ?? null
  }
}

export type ApiFetchInit = Omit<RequestInit, 'body'> & {
  /** Override the default request timeout (ms). */
  timeoutMs?: number
  /** Plain objects are JSON-stringified; strings/Blob/FormData pass through. */
  body?: BodyInit | Record<string, unknown> | null
}

export function useApi() {
  const config = useRuntimeConfig()
  const correlationId = useState<string>('correlation-id', () => crypto.randomUUID())
  // Read shared auth/org state directly - calling useAuth/useOrgs here creates
  // useApi → useAuth → useOrgs → useApi and overflows the call stack.
  const token = useState<string | null>('auth-token', () => null)
  const activeOrgId = useState<string | null>('active-org-id', () => null)
  const DEFAULT_REQUEST_TIMEOUT_MS = 20_000

  async function apiFetch<T>(path: string, init: ApiFetchInit = {}): Promise<T> {
    const { timeoutMs, ...requestInit } = init
    const headers = new Headers(requestInit.headers)
    headers.set('Accept', 'application/json')
    headers.set('X-Correlation-ID', correlationId.value)

    let body = requestInit.body
    if (
      body !== undefined
      && body !== null
      && typeof body === 'object'
      && !(body instanceof Blob)
      && !(body instanceof ArrayBuffer)
      && !(body instanceof FormData)
      && !(body instanceof URLSearchParams)
      && !(body instanceof ReadableStream)
      && !ArrayBuffer.isView(body)
    ) {
      body = JSON.stringify(body)
    }
    if (body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    if (token.value) {
      headers.set('Authorization', `Bearer ${token.value}`)
    }
    if (activeOrgId.value) {
      headers.set('X-Org-ID', activeOrgId.value)
    }

    const timeoutController = new AbortController()
    const timeoutHandle = setTimeout(() => {
      timeoutController.abort()
    }, timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS)
    const mergedController = new AbortController()
    let clientAborted = false
    let timeoutAborted = false

    const onClientAbort = () => {
      clientAborted = true
      mergedController.abort()
    }
    const onTimeoutAbort = () => {
      timeoutAborted = true
      mergedController.abort()
    }
    requestInit.signal?.addEventListener('abort', onClientAbort, { once: true })
    timeoutController.signal.addEventListener('abort', onTimeoutAbort, { once: true })

    let response: Response
    try {
      response = await fetch(`${config.public.apiBase}${path}`, {
        ...requestInit,
        body,
        headers,
        signal: mergedController.signal,
      })
    } catch (error) {
      if (timeoutAborted && !clientAborted) {
        throw new Error('Request timed out while contacting the control plane')
      }
      throw error
    } finally {
      clearTimeout(timeoutHandle)
      requestInit.signal?.removeEventListener('abort', onClientAbort)
      timeoutController.signal.removeEventListener('abort', onTimeoutAbort)
    }

    const responseCorrelation = response.headers.get('X-Correlation-ID')
    if (responseCorrelation) {
      correlationId.value = responseCorrelation
    }

    if (response.status === 401) {
      // Lazy import path: logout only after the cycle-safe state reads above.
      useAuth().logout()
      if (import.meta.client) {
        await navigateTo('/login')
      }
      throw new ApiError(401, null, 'Authentication required')
    }

    if (!response.ok) {
      let body: ApiErrorBody | null = null
      try {
        body = (await response.json()) as ApiErrorBody
      } catch {
        body = null
      }
      throw new ApiError(response.status, body, `Request failed (${response.status})`)
    }

    if (response.status === 204) {
      return undefined as T
    }

    return (await response.json()) as T
  }

  return { apiFetch, correlationId }
}
