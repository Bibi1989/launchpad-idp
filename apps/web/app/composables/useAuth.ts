import type { AuthConfig, AuthUser, MeResponse, TokenResponse } from '~/types/auth'
import type { LoginInput, RegisterInput } from '~/utils/authValidation'

const TOKEN_KEY = 'launchpad_access_token'

export function useAuth() {
  const config = useRuntimeConfig()
  const token = useState<string | null>('auth-token', () => null)
  const user = useState<AuthUser | null>('auth-user', () => null)
  const authConfig = useState<AuthConfig | null>('auth-config', () => null)
  const ready = useState<boolean>('auth-ready', () => false)
  const { loadActiveOrgFromStorage, applyFromTokenResponse, setActiveOrg } = useOrgs()

  function loadTokenFromStorage() {
    if (!import.meta.client) return
    const stored = localStorage.getItem(TOKEN_KEY)
    if (stored) {
      token.value = stored
    }
  }

  function persistToken(accessToken: string | null) {
    token.value = accessToken
    if (!import.meta.client) return
    if (accessToken) {
      localStorage.setItem(TOKEN_KEY, accessToken)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }

  async function fetchConfig() {
    const response = await fetch(`${config.public.apiBase}/auth/config`)
    if (!response.ok) {
      authConfig.value = { dev_login_enabled: false, oidc_enabled: false }
      return
    }
    authConfig.value = (await response.json()) as AuthConfig
  }

  async function refreshMe() {
    if (!token.value) {
      user.value = null
      return
    }
    const response = await fetch(`${config.public.apiBase}/auth/me`, {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${token.value}`,
      },
    })
    if (!response.ok) {
      persistToken(null)
      user.value = null
      return
    }
    const body = (await response.json()) as MeResponse
    user.value = body.user
    applyFromTokenResponse(body)
  }

  async function init() {
    loadTokenFromStorage()
    loadActiveOrgFromStorage()
    try {
      await fetchConfig()
      await refreshMe()
    } catch {
      // Control plane may be down during boot; still unblock the UI shell.
      if (!authConfig.value) {
        authConfig.value = { dev_login_enabled: false, oidc_enabled: false }
      }
    } finally {
      ready.value = true
    }
  }

  async function applyTokenResponse(body: TokenResponse): Promise<TokenResponse> {
    persistToken(body.access_token)
    user.value = body.user
    applyFromTokenResponse(body)
    return body
  }

  async function login(payload: LoginInput): Promise<TokenResponse> {
    const response = await fetch(`${config.public.apiBase}/auth/login`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => null)
      throw new Error(err?.error?.message ?? 'Login failed')
    }
    return applyTokenResponse((await response.json()) as TokenResponse)
  }

  async function register(payload: RegisterInput): Promise<TokenResponse> {
    const response = await fetch(`${config.public.apiBase}/auth/register`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => null)
      throw new Error(err?.error?.message ?? 'Registration failed')
    }
    return applyTokenResponse((await response.json()) as TokenResponse)
  }

  async function devLogin(): Promise<TokenResponse> {
    const response = await fetch(`${config.public.apiBase}/auth/dev-login`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) {
      const err = await response.json().catch(() => null)
      throw new Error(err?.error?.message ?? 'Dev login failed')
    }
    return applyTokenResponse((await response.json()) as TokenResponse)
  }

  async function startOidcLogin() {
    const response = await fetch(`${config.public.apiBase}/auth/oidc/start`)
    if (!response.ok) {
      const err = await response.json().catch(() => null)
      throw new Error(err?.error?.message ?? 'OIDC start failed')
    }
    const body = (await response.json()) as { authorization_url: string; state: string }
    if (import.meta.client) {
      sessionStorage.setItem('launchpad_oidc_state', body.state)
      window.location.href = body.authorization_url
    }
  }

  function logout() {
    persistToken(null)
    user.value = null
    setActiveOrg(null)
  }

  return {
    token,
    user,
    authConfig,
    ready,
    init,
    login,
    register,
    devLogin,
    startOidcLogin,
    logout,
    refreshMe,
  }
}
