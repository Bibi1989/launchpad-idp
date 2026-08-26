import {
  pluginManifestPayloadSchema,
  type PluginConfigTool,
  type PluginFieldError,
  type PluginIacEngine,
  type PluginManifestForm,
  type PluginManifestPayload,
  type PluginPhaseRunner,
  type PluginRunnerType,
  type PluginServiceType,
} from '~/types/pluginManifest'

const IAC_RUNNERS: ReadonlySet<PluginRunnerType> = new Set([
  'terraform',
  'opentofu',
  'pulumi',
  'ansible',
])

const SERVICE_TO_RUNTIME: Record<PluginServiceType, string> = {
  vm: 'vm',
  container: 'docker_host',
  kubernetes: 'kubernetes',
  paas: 'paas',
}

export function slugifyPluginId(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || 'plugin'
}

export function defaultPluginForm(): PluginManifestForm {
  return {
    id: 'digitalocean-droplet',
    label: 'DigitalOcean Droplets',
    version: '1.0.0',
    category: 'cloud-provider',
    description: 'Provision DigitalOcean droplets with Terraform.',
    icon: 'water_drop',
    owner: 'user',
    visibility: 'private',
    runnerType: 'terraform',
    runnerTarget: 'digitalocean',
    useStackRunners: false,
    provisionRunnerType: 'terraform',
    provisionRunnerTarget: 'provision',
    configRunnerType: 'ansible',
    configRunnerTarget: 'config/site.yml',
    defaultIacEngine: 'launchpad',
    defaultConfigTool: 'cloud-init',
    serviceType: 'vm',
    supportsTtl: true,
    supportsCustomDns: true,
    supportsEphemeralDb: false,
    credentialsSchema: {},
    deploymentConfigSchema: {},
    docsUrl: '',
    homepage: '',
    license: '',
    author: '',
    keywords: [],
    parentCloud: '',
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function isRunnerType(value: string): value is PluginRunnerType {
  return ['terraform', 'opentofu', 'pulumi', 'ansible', 'node', 'python', 'script'].includes(value)
}

function isServiceType(value: string): value is PluginServiceType {
  return ['vm', 'container', 'kubernetes', 'paas'].includes(value)
}

export function isIacRunner(type: PluginRunnerType): boolean {
  return IAC_RUNNERS.has(type)
}

export function credentialFieldsFromSchema(
  schema: Record<string, unknown>,
): PluginManifestPayload['credential_fields'] {
  const props = asRecord(schema.properties)
  const required = new Set(
    Array.isArray(schema.required) ? schema.required.map((item) => String(item)) : [],
  )
  return Object.entries(props).map(([name, raw]) => {
    const prop = asRecord(raw)
    const hint = `${name} ${str(prop.title)}`.toLowerCase()
    const secret =
      prop.writeOnly === true ||
      hint.includes('token') ||
      hint.includes('secret') ||
      hint.includes('password') ||
      hint.includes('key')
    return {
      name,
      label: str(prop.title, name),
      secret,
      required: required.has(name),
      help: str(prop.description) || null,
    }
  })
}

function isIacEngine(value: string): value is PluginIacEngine {
  return ['launchpad', 'terraform', 'opentofu', 'pulumi', 'ansible'].includes(value)
}

function isConfigTool(value: string): value is PluginConfigTool {
  return ['cloud-init', 'ansible', 'puppet', 'chef'].includes(value)
}

function phaseRunnerPayload(
  type: PluginRunnerType,
  target: string,
): PluginPhaseRunner {
  const runner: PluginPhaseRunner = { type }
  const trimmed = target.trim()
  if (!trimmed) return runner
  if (type === 'ansible') runner.playbookPath = trimmed
  else if (isIacRunner(type)) runner.bundlePath = trimmed
  else runner.entry = trimmed
  return runner
}

function legacyRunnerFromForm(form: PluginManifestForm): PluginManifestPayload['runner'] {
  const type = form.useStackRunners ? form.provisionRunnerType : form.runnerType
  const target = form.useStackRunners ? form.provisionRunnerTarget : form.runnerTarget
  return phaseRunnerPayload(type, target) as PluginManifestPayload['runner']
}

export function compilePluginManifest(form: PluginManifestForm): PluginManifestPayload {
  const runner = legacyRunnerFromForm(form)
  const payload: PluginManifestPayload = {
    id: slugifyPluginId(form.id),
    label: form.label.trim(),
    version: form.version.trim() || '1.0.0',
    category: form.category,
    description: form.description.trim(),
    icon: form.icon.trim(),
    runner,
    capabilities: {
      serviceType: form.serviceType,
      supportsTtl: form.supportsTtl,
      supportsCustomDns: form.supportsCustomDns,
      supportsEphemeralDb: form.supportsEphemeralDb,
    },
    credentialsSchema: form.credentialsSchema,
    deploymentConfigSchema: form.deploymentConfigSchema,
    runtime_targets: [SERVICE_TO_RUNTIME[form.serviceType]],
    credential_fields: credentialFieldsFromSchema(form.credentialsSchema),
    ...(form.docsUrl?.trim() ? { docsUrl: form.docsUrl.trim() } : {}),
    ...(form.homepage?.trim() ? { homepage: form.homepage.trim() } : {}),
    ...(form.license?.trim() ? { license: form.license.trim() } : {}),
    ...(form.author?.trim() ? { author: form.author.trim() } : {}),
    ...((form.keywords?.length ?? 0) > 0 ? { keywords: form.keywords } : {}),
    ...(form.parentCloud?.trim() ? { parentCloud: form.parentCloud.trim() } : {}),
  }
  if (form.useStackRunners) {
    payload.runners = {
      provision: phaseRunnerPayload(form.provisionRunnerType, form.provisionRunnerTarget),
      config: phaseRunnerPayload(form.configRunnerType, form.configRunnerTarget),
    }
  }
  payload.defaults = {
    iacEngine: form.defaultIacEngine,
    configTool: form.defaultConfigTool,
  }
  return payload
}

export function validateCompiledManifest(payload: PluginManifestPayload): PluginFieldError[] {
  const result = pluginManifestPayloadSchema.safeParse(payload)
  if (result.success) return []
  return result.error.issues.map((issue) => ({
    loc: issue.path.join('.') || '(root)',
    msg: issue.message,
  }))
}

function hydratePhaseRunner(raw: Record<string, unknown>): { type: PluginRunnerType; target: string } {
  const runnerType =
    str(raw.type) || str(raw.engine) || str(raw.runtime) || str(raw.kind) || 'terraform'
  const type = isRunnerType(runnerType) ? runnerType : 'terraform'
  const target =
    str(raw.bundlePath) ||
    str(raw.bundle_path) ||
    str(raw.working_dir) ||
    str(raw.workingDir) ||
    str(raw.project_dir) ||
    str(raw.playbook_path) ||
    str(raw.playbookPath) ||
    str(raw.entry) ||
    str(raw.entrypoint)
  return { type, target }
}

export function hydratePluginForm(raw: Record<string, unknown>): PluginManifestForm {
  const form = defaultPluginForm()
  const label = str(raw.label) || str(raw.displayName) || str(raw.name) || str(raw.title)
  form.label = label || form.label
  form.id = slugifyPluginId(str(raw.id) || label || form.id)
  form.version = str(raw.version, '1.0.0')
  const category = str(raw.category)
  if (category === 'ingress' || category === 'notification' || category === 'database' || category === 'cloud-provider' || category === 'config') {
    form.category = category
  }
  form.description = str(raw.description) || str(raw.summary)
  form.icon = str(raw.icon) || form.icon
  const owner = str(raw.owner)
  if (owner === 'user' || owner === 'organization') form.owner = owner
  const visibility = str(raw.visibility)
  if (visibility === 'public' || visibility === 'private') form.visibility = visibility
  const runner = asRecord(raw.runner) || asRecord(raw.runtime)
  const runnerType =
    str(runner.type) || str(runner.engine) || str(runner.runtime) || str(runner.kind)
  if (isRunnerType(runnerType)) form.runnerType = runnerType
  form.runnerTarget =
    str(runner.bundlePath) ||
    str(runner.bundle_path) ||
    str(runner.working_dir) ||
    str(runner.workingDir) ||
    str(runner.project_dir) ||
    str(runner.playbook_path) ||
    str(runner.entry) ||
    str(runner.entrypoint)
  const caps = raw.capabilities
  if (caps && typeof caps === 'object' && !Array.isArray(caps)) {
    const rec = asRecord(caps)
    const service = str(rec.serviceType) || str(rec.service_type)
    if (isServiceType(service)) form.serviceType = service
    form.supportsTtl = bool(rec.supportsTtl ?? rec.supports_ttl)
    form.supportsCustomDns = bool(rec.supportsCustomDns ?? rec.supports_custom_dns)
    form.supportsEphemeralDb = bool(rec.supportsEphemeralDb ?? rec.supports_ephemeral_db)
  } else if (Array.isArray(caps)) {
    const labels = caps.map(String)
    const service = labels.find((item) => isServiceType(item))
    if (service && isServiceType(service)) form.serviceType = service
    form.supportsTtl = labels.includes('supportsTtl')
    form.supportsCustomDns = labels.includes('supportsCustomDns')
    form.supportsEphemeralDb = labels.includes('supportsEphemeralDb')
  }
  form.credentialsSchema = asRecord(raw.credentialsSchema || raw.credentials_schema)
  form.deploymentConfigSchema = asRecord(raw.deploymentConfigSchema || raw.deployment_config_schema)
  form.docsUrl = str(raw.docsUrl) || str(raw.docs_url)
  form.homepage = str(raw.homepage) || str(raw.home_page) || str(raw.url)
  form.license = str(raw.license) || str(raw.licence)
  form.author = str(raw.author)
  const keywords = raw.keywords
  form.keywords = Array.isArray(keywords)
    ? keywords.map((item) => String(item).trim()).filter(Boolean)
    : str(raw.keywords)
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
  form.parentCloud = str(raw.parentCloud) || str(raw.parent_cloud)

  const runnersRaw = asRecord(raw.runners)
  const provisionRaw = asRecord(runnersRaw.provision)
  const configRaw = asRecord(runnersRaw.config)
  form.useStackRunners = Boolean(provisionRaw.type || configRaw.type)
  if (form.useStackRunners) {
    const provision = hydratePhaseRunner(provisionRaw.type ? provisionRaw : asRecord(raw.runner))
    form.provisionRunnerType = provision.type
    form.provisionRunnerTarget = provision.target
    const config = hydratePhaseRunner(configRaw)
    form.configRunnerType = config.type
    form.configRunnerTarget = config.target
    form.runnerType = provision.type
    form.runnerTarget = provision.target
  }

  const defaultsRaw = asRecord(raw.defaults)
  const iacEngine = str(defaultsRaw.iacEngine) || str(defaultsRaw.iac_engine)
  if (isIacEngine(iacEngine)) form.defaultIacEngine = iacEngine
  const configTool = str(defaultsRaw.configTool) || str(defaultsRaw.config_tool)
  if (isConfigTool(configTool)) form.defaultConfigTool = configTool

  return form
}

export function errorForField(errors: PluginFieldError[], ...keys: string[]): string | null {
  const hit = errors.find((err) => keys.some((key) => err.loc === key || err.loc.startsWith(`${key}.`)))
  return hit ? `${hit.loc}: ${hit.msg}` : null
}

export function schemaEditorErrors(errors: PluginFieldError[]): {
  credentials: string | null
  deployment: string | null
} {
  return {
    credentials: errorForField(errors, 'credentialsSchema', 'credentials_schema'),
    deployment: errorForField(errors, 'deploymentConfigSchema', 'deployment_config_schema'),
  }
}

export function pluginSchemaGeneratePayload(
  form: PluginManifestForm,
  hint = '',
): {
  parent_cloud: string
  service_type: string
  plugin_id: string
  label: string
  category: string
  description: string
  prompt: string
} {
  return {
    parent_cloud: form.parentCloud.trim(),
    service_type: form.serviceType,
    plugin_id: slugifyPluginId(form.id),
    label: form.label.trim(),
    category: form.category,
    description: form.description.trim(),
    prompt: hint.trim(),
  }
}
