const LOCAL_HOSTS = ['127.0.0.1', 'localhost', '::1', 'localtest.me']

/**
 * Rewrite a loopback (or missing) host in a URL to the current browser host,
 * keeping the port. A stored URL with an empty host (e.g. "http://:30087",
 * produced when the backend has no preview host configured) is repaired to the
 * host the user is currently reaching Launchpad on. When we take over the host
 * we also adopt the page's scheme, so an http:// preview served to an https://
 * Launchpad becomes https:// - otherwise the browser blocks it as mixed content.
 */
function rewriteLoopbackHost(url: string): string | null {
  if (!url) return null
  if (typeof window === 'undefined') return url
  const pageScheme = window.location.protocol // "http:" | "https:"
  // Repair a hostless URL like "http://:30087" up front: new URL() throws on an
  // empty host for special schemes, so inject the current host before parsing.
  const hostless = url.match(/^([a-z][a-z0-9+.-]*:\/\/):(\d+)(.*)$/i)
  if (hostless) {
    url = `${pageScheme}//${window.location.hostname}:${hostless[2]}${hostless[3]}`
  }
  try {
    const parsed = new URL(url)
    if (!parsed.hostname || LOCAL_HOSTS.includes(parsed.hostname)) {
      parsed.hostname = window.location.hostname
      parsed.protocol = pageScheme
    }
    return parsed.toString().replace(/\/$/, '')
  } catch {
    return url
  }
}

export interface PreviewUrlSource {
  preview_url?: string | null
  node_port?: number | null
  provider?: string | null
}

/**
 * Resolve the "Open app" URL for how the user is *currently* reaching Launchpad:
 *
 *  - Local preview, viewed from localhost  → http://localhost:<node_port> (direct NodePort).
 *  - Local preview, viewed via a tunnel/remote host → the stored public URL
 *    (e.g. a cloudflared *.trycloudflare.com tunnel), since 127.0.0.1 won't reach it.
 *  - Cloud/production preview → the stored public URL (LoadBalancer / Ingress).
 *
 * Accepts an environment-like object (preferred) or a bare URL string (back-compat).
 */
export function resolvePreviewUrl(
  source: PreviewUrlSource | string | null | undefined,
): string | null {
  if (source == null) return null
  if (typeof source === 'string') return rewriteLoopbackHost(source)
  if (typeof window === 'undefined') return source.preview_url ?? null

  const host = window.location.hostname
  const isLocalViewer = LOCAL_HOSTS.includes(host)
  const provider = (source.provider || '').toLowerCase()

  // Viewing a local preview from the same machine: hit the NodePort directly on the
  // browser's own host rather than routing a dev out through a public tunnel URL.
  if (provider === 'local' && isLocalViewer && source.node_port) {
    return `http://${host}:${source.node_port}`
  }

  // Otherwise prefer the stored public URL (cloudflared tunnel / cloud LB / ingress),
  // rewriting a loopback host to the current one when the preview is still a NodePort.
  if (source.preview_url) return rewriteLoopbackHost(source.preview_url)

  // Last resort: build a NodePort URL on the current host, following the page
  // scheme so an https Launchpad doesn't hand out a mixed-content http link.
  if (source.node_port) return `${window.location.protocol}//${host}:${source.node_port}`
  return null
}
