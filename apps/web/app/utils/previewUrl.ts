const LOCAL_HOSTS = ['127.0.0.1', 'localhost', '::1', 'localtest.me']
const K8S_DEPLOY_MODES = new Set(['preview', 'manifest'])

function extractPort(url: string): number | null {
  try {
    const parsed = new URL(url)
    if (parsed.port) {
      const n = Number.parseInt(parsed.port, 10)
      return Number.isFinite(n) ? n : null
    }
  } catch {
    /* ignore */
  }
  return null
}

function isLoopbackHost(hostname: string): boolean {
  return LOCAL_HOSTS.includes(hostname)
}

function usesWorkspaceIngress(deployMode?: string | null): boolean {
  return K8S_DEPLOY_MODES.has((deployMode || '').toLowerCase())
}

/**
 * Rewrite a loopback (or missing) host in a URL to the current browser host,
 * keeping the port. Only valid when the viewer is on the same machine as the
 * NodePort (localhost). Never rewrite onto a remote Launchpad UI host.
 */
function rewriteLoopbackHost(url: string, viewerHost: string): string | null {
  if (!url || !viewerHost || !isLoopbackHost(viewerHost)) return url
  const pageScheme =
    typeof window !== 'undefined' ? window.location.protocol : 'http:'
  const hostless = url.match(/^([a-z][a-z0-9+.-]*:\/\/):(\d+)(.*)$/i)
  if (hostless) {
    url = `${pageScheme}//${viewerHost}:${hostless[2]}${hostless[3]}`
  }
  try {
    const parsed = new URL(url)
    if (!parsed.hostname || isLoopbackHost(parsed.hostname)) {
      parsed.hostname = viewerHost
      parsed.protocol = pageScheme
    }
    return parsed.toString().replace(/\/$/, '')
  } catch {
    return url
  }
}

/**
 * Production Cloudflare ingress: ``https://ws-{envId}.{apex}``.
 * Stored NodePort mistakes look like ``http://launchpad-idp.online:2001``.
 */
function workspaceIngressUrl(envId: string, apexHost: string): string {
  return `https://ws-${envId}.${apexHost}`
}

function looksLikeBrokenApexNodePort(url: string, viewerHost: string): boolean {
  if (!url || !viewerHost || isLoopbackHost(viewerHost)) return false
  try {
    const parsed = new URL(url)
    if (parsed.hostname.endsWith('.trycloudflare.com')) return false
    if (parsed.hostname.startsWith('ws-') && parsed.hostname.endsWith(`.${viewerHost}`)) {
      return false
    }
    if (parsed.hostname === viewerHost && parsed.port) return true
    if (isLoopbackHost(parsed.hostname) && parsed.port) return true
    if (!parsed.hostname && parsed.port) return true
    return false
  } catch {
    return /:\/\/:\d+/.test(url)
  }
}

export interface PreviewUrlSource {
  id?: string | null
  preview_url?: string | null
  node_port?: number | null
  provider?: string | null
  deploy_mode?: string | null
}

export interface LocalizePreviewUrlInput {
  url: string
  port?: number | null
  provider?: string | null
  deployMode?: string | null
  environmentId?: string | null
  /** Override browser hostname (for tests). */
  viewerHost?: string
}

/**
 * When the operator is on localhost viewing a local preview, prefer
 * ``http://localhost:<port>`` over a stored public tunnel URL
 * (``*.trycloudflare.com``). Remote viewers keep / repair the public URL.
 */
export function localizePreviewUrl(input: LocalizePreviewUrlInput): string {
  const url = (input.url || '').trim()
  if (!url) return url
  const viewerHost = input.viewerHost
    ?? (typeof window !== 'undefined' ? window.location.hostname : '')
  const isLocalViewer = isLoopbackHost(viewerHost)
  const provider = (input.provider || '').toLowerCase()
  const port = input.port ?? extractPort(url)

  if ((provider === 'local' || !provider) && isLocalViewer && port) {
    return `http://${viewerHost}:${port}`
  }

  // Repair mistaken apex:port / loopback NodePort URLs for remote k8s viewers only.
  // Attach/compose must not invent ws-* hosts (no Ingress → Cloudflare 404).
  if (
    !isLocalViewer
    && input.environmentId
    && usesWorkspaceIngress(input.deployMode)
    && looksLikeBrokenApexNodePort(url, viewerHost)
  ) {
    return workspaceIngressUrl(input.environmentId, viewerHost)
  }

  // Public quick tunnels must not keep a host:port suffix (broken links).
  try {
    const parsed = new URL(url)
    if (parsed.hostname.endsWith('.trycloudflare.com') && parsed.port) {
      parsed.port = ''
      parsed.protocol = 'https:'
      return parsed.toString().replace(/\/$/, '')
    }
  } catch {
    /* fall through */
  }

  if (isLocalViewer) {
    return rewriteLoopbackHost(url, viewerHost) ?? url
  }
  return url
}

/**
 * Resolve the "Open app" URL for how the user is *currently* reaching Launchpad.
 */
export function resolvePreviewUrl(
  source: PreviewUrlSource | string | null | undefined,
): string | null {
  if (source == null) return null
  if (typeof source === 'string') {
    return localizePreviewUrl({ url: source, provider: 'local' })
  }

  if (typeof window === 'undefined') {
    return source.preview_url ?? null
  }

  const host = window.location.hostname
  const isLocalViewer = isLoopbackHost(host)
  const provider = (source.provider || '').toLowerCase()

  if (provider === 'local' && isLocalViewer && source.node_port) {
    return `http://${host}:${source.node_port}`
  }

  if (source.preview_url) {
    return localizePreviewUrl({
      url: source.preview_url,
      port: source.node_port,
      provider: source.provider,
      deployMode: source.deploy_mode,
      environmentId: source.id,
      viewerHost: host,
    })
  }

  // Remote k8s viewers: prefer workspace ingress over inventing apex:node_port.
  if (
    source.node_port
    && source.id
    && !isLocalViewer
    && usesWorkspaceIngress(source.deploy_mode)
  ) {
    return workspaceIngressUrl(source.id, host)
  }

  if (source.node_port && isLocalViewer) {
    return `${window.location.protocol}//${host}:${source.node_port}`
  }
  return null
}
