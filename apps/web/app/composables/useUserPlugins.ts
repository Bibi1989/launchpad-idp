/**
 * Register / remove declarative cloud plugins (manifests) for the current org.
 * Backed by:
 *   POST   /plugins/validate               (dry-run PluginManifest validation)
 *   POST   /plugins/register               (persist / replace a manifest)
 *   GET    /plugins/{id}                   (stored manifest JSON for edit)
 *   DELETE /plugins/{id}                   (remove)
 * Legacy aliases under /cloud-providers/plugins remain valid.
 */
import type { PluginFieldError, PluginValidateResponse } from '~/types/pluginManifest'

export function useUserPlugins() {
  const { apiFetch } = useApi()

  const saving = useState<boolean>('user-plugin-saving', () => false)
  const error = useState<string | null>('user-plugin-error', () => null)

  async function register(
    manifest: Record<string, unknown>,
    options?: { owner?: 'user' | 'organization'; visibility?: 'private' | 'public' },
  ): Promise<void> {
    saving.value = true
    error.value = null
    try {
      await apiFetch('/plugins/register', {
        method: 'POST',
        body: JSON.stringify({
          manifest,
          owner: options?.owner ?? 'organization',
          visibility: options?.visibility ?? 'private',
        }),
      })
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to register plugin'
      throw err
    } finally {
      saving.value = false
    }
  }

  async function get(pluginId: string): Promise<{
    manifest: Record<string, unknown>
    owner: 'user' | 'organization'
    visibility: 'private' | 'public'
    can_edit: boolean
  } | null> {
    const res = await apiFetch<{
      manifest: Record<string, unknown>
      owner?: 'user' | 'organization'
      visibility?: 'private' | 'public'
      can_edit?: boolean
    }>(`/plugins/${pluginId}`)
    if (!res.manifest) return null
    return {
      manifest: res.manifest,
      owner: res.owner === 'user' ? 'user' : 'organization',
      visibility: res.visibility === 'public' ? 'public' : 'private',
      can_edit: Boolean(res.can_edit),
    }
  }

  async function remove(pluginId: string): Promise<void> {
    await apiFetch(`/plugins/${pluginId}`, { method: 'DELETE' })
  }

  /** Dry-run validation. Returns field-level errors without persisting. */
  async function validate(manifest: Record<string, unknown>): Promise<PluginValidateResponse> {
    const res = await apiFetch<PluginValidateResponse>('/plugins/validate', {
      method: 'POST',
      body: JSON.stringify({ manifest }),
    })
    return {
      valid: Boolean(res.valid),
      errors: Array.isArray(res.errors) ? (res.errors as PluginFieldError[]) : [],
      manifest: res.manifest ?? null,
    }
  }

  async function uploadBundle(pluginId: string, file: File): Promise<{ files: number }> {
    const form = new FormData()
    form.append('file', file)
    return apiFetch<{ files: number }>(`/cloud-providers/plugins/${pluginId}/bundle`, {
      method: 'POST',
      body: form,
      timeoutMs: 60_000,
    })
  }

  async function generate(prompt: string): Promise<{
    manifest: Record<string, unknown>
    source: string
    gemini_configured: boolean
  }> {
    return apiFetch('/plugins/generate', {
      method: 'POST',
      body: JSON.stringify({ prompt }),
      timeoutMs: 60_000,
    })
  }

  async function generateSchemas(payload: {
    parent_cloud?: string
    service_type?: string
    plugin_id?: string
    label?: string
    category?: string
    description?: string
    prompt?: string
  }): Promise<{
    credentialsSchema: Record<string, unknown>
    deploymentConfigSchema: Record<string, unknown>
    source: string
    gemini_configured: boolean
  }> {
    return apiFetch('/plugins/generate-schemas', {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: 60_000,
    })
  }

  return { saving, error, register, get, remove, validate, uploadBundle, generate, generateSchemas }
}
