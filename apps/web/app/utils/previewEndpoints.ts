import type { Environment, PreviewEndpoint } from '~/types/environment'
import { localizePreviewUrl } from '~/utils/previewUrl'

function rawPreviewEndpoints(env: Environment): PreviewEndpoint[] {
  const fromApi = env.preview_endpoints
  if (Array.isArray(fromApi) && fromApi.length > 0) {
    return fromApi.filter((e) => Boolean(e?.url))
  }
  if (env.preview_endpoints_json) {
    try {
      const parsed = JSON.parse(env.preview_endpoints_json) as unknown
      if (Array.isArray(parsed)) {
        return parsed.filter(
          (e): e is PreviewEndpoint =>
            Boolean(e && typeof e === 'object' && typeof (e as PreviewEndpoint).url === 'string'),
        )
      }
    } catch {
      /* ignore */
    }
  }
  if (env.preview_url) {
    return [
      {
        name: 'app',
        app_kind: 'frontend',
        url: env.preview_url,
        port: env.node_port ?? null,
        exposed: true,
      },
    ]
  }
  return []
}

/** Normalize API / SSE preview endpoint lists for Open-app UI. */
export function resolvePreviewEndpoints(env: Environment): PreviewEndpoint[] {
  return rawPreviewEndpoints(env).map((endpoint) => {
    const port = endpoint.port
      ?? (endpoint.app_kind === 'frontend' ? env.node_port : null)
      ?? null
    return {
      ...endpoint,
      url: localizePreviewUrl({
        url: endpoint.url,
        port,
        provider: env.provider ?? 'local',
        deployMode: env.deploy_mode,
        environmentId: env.id,
      }),
      port,
    }
  })
}

/** Secondary exposed URLs (API, etc.). Open app stays on the frontend. */
export function secondaryPreviewEndpoints(env: Environment): PreviewEndpoint[] {
  const all = resolvePreviewEndpoints(env)
  return all.filter((e) => {
    if (!e.exposed && e.exposed !== undefined) return false
    if (e.app_kind === 'frontend') return false
    return true
  })
}
