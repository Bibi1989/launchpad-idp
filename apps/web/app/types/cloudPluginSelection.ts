/**
 * A cloud-plugin deploy target chosen in a create/update flow
 * (workspace, environment, launch). Credentials are NOT part of this - they live in
 * Settings; this only records what the user picked.
 */
export interface CloudPluginSelection {
  provider: string | null
  service: string | null
  region: string | null
  tier: string | null
}

export function emptyCloudPluginSelection(): CloudPluginSelection {
  return { provider: null, service: null, region: null, tier: null }
}
