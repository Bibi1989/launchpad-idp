const LOCAL_HOSTS = ['127.0.0.1', 'localhost', '::1', 'localtest.me']

/** Rewrite a loopback host in a URL to the current browser host (keeps scheme/port). */
function rewriteLoopbackHost(url: string): string | null {
  if (!url) return null
  if (typeof window === 'undefined') return url
  try {
    const parsed = new URL(url)
    if (LOCAL_HOSTS.includes(parsed.hostname)) {
      parsed.hostname = window.location.hostname
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

  // Last resort: build a NodePort URL on the current host.
  if (source.node_port) return `http://${host}:${source.node_port}`
  return null
}
