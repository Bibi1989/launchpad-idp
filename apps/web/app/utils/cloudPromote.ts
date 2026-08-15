import type { ContainerServiceItem } from '~/types/provisioning'

const FRONTEND_TOKENS = new Set([
  'web',
  'ui',
  'frontend',
  'spa',
  'next',
  'nuxt',
  'client',
  'nextjs',
  'nuxtjs',
  'react',
  'vue',
  'svelte',
])

const BACKEND_TOKENS = new Set([
  'api',
  'backend',
  'server',
  'svc',
  'service',
  'worker',
  'fastapi',
  'express',
  'nest',
  'django',
  'flask',
])

function serviceNameTokens(name: string): Set<string> {
  return new Set(
    name
      .trim()
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean),
  )
}

export function isFrontendServiceName(name: string): boolean {
  const parts = serviceNameTokens(name)
  if ([...parts].some((p) => BACKEND_TOKENS.has(p)) && ![...parts].some((p) => FRONTEND_TOKENS.has(p))) {
    return false
  }
  if ([...parts].some((p) => FRONTEND_TOKENS.has(p))) {
    return true
  }
  const joined = name.trim().toLowerCase()
  return joined.startsWith('web') || joined.includes('-web-') || joined.endsWith('-web')
}

export function isFrontendAppKind(appKind: string | null | undefined, name = ''): boolean {
  const kind = (appKind ?? '').trim().toLowerCase()
  if (kind === 'frontend') return true
  if (kind === 'backend') return false
  return isFrontendServiceName(name)
}

/** Match backend recommend_primary_service: frontend first, else first service. */
export function recommendPrimaryService(services: ContainerServiceItem[]): string | null {
  if (!services.length) return null
  for (const spec of services) {
    if (isFrontendAppKind(spec.app_kind, spec.name)) {
      return spec.name
    }
  }
  return services[0]?.name ?? null
}
