import type { CloudProviderCatalogEntry } from '~/types/cloudProviders'
import type { PluginConfigTool, PluginIacEngine } from '~/types/pluginManifest'
import type { IaCEngine, InstanceConfigTool } from '~/types/provisioning'

const IAC_ENGINES: ReadonlySet<string> = new Set([
  'launchpad',
  'terraform',
  'opentofu',
  'pulumi',
  'ansible',
])

const CONFIG_TOOLS: ReadonlySet<string> = new Set(['cloud-init', 'ansible', 'puppet', 'chef'])

function catalogDefaults(entry: CloudProviderCatalogEntry | null | undefined): {
  iacEngine?: PluginIacEngine
  configTool?: PluginConfigTool
} {
  const raw = entry?.defaults
  if (!raw || typeof raw !== 'object') return {}
  const iacRaw = (raw as Record<string, unknown>).iacEngine ?? (raw as Record<string, unknown>).iac_engine
  const configRaw = (raw as Record<string, unknown>).configTool ?? (raw as Record<string, unknown>).config_tool
  const out: { iacEngine?: PluginIacEngine; configTool?: PluginConfigTool } = {}
  if (typeof iacRaw === 'string' && IAC_ENGINES.has(iacRaw)) {
    out.iacEngine = iacRaw as PluginIacEngine
  }
  if (typeof configRaw === 'string' && CONFIG_TOOLS.has(configRaw)) {
    out.configTool = configRaw as PluginConfigTool
  }
  return out
}

/** Apply manifest ``defaults`` when a registered stack plugin is chosen as deploy target. */
export function applyPluginManifestDefaults(
  form: {
    iac_engine: IaCEngine
    config_tool: InstanceConfigTool
  },
  entry: CloudProviderCatalogEntry | null | undefined,
  opts?: { iacEngineTouched?: boolean },
): void {
  const defaults = catalogDefaults(entry)
  if (!defaults.iacEngine && !defaults.configTool) return
  if (defaults.iacEngine && !opts?.iacEngineTouched) {
    form.iac_engine = defaults.iacEngine
  }
  if (defaults.configTool) {
    form.config_tool = defaults.configTool
  }
}
