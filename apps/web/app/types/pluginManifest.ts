import { z } from 'zod'

export const pluginCategorySchema = z.enum([
  'cloud-provider',
  'ingress',
  'notification',
  'database',
  'config',
])
export type PluginCategory = z.infer<typeof pluginCategorySchema>

export const pluginRunnerTypeSchema = z.enum([
  'terraform',
  'opentofu',
  'pulumi',
  'ansible',
  'node',
  'python',
  'script',
])
export type PluginRunnerType = z.infer<typeof pluginRunnerTypeSchema>

/** Wizard engines a stack plugin may preselect (LaunchProvision = launchpad). */
export const pluginIacEngineSchema = z.enum(['launchpad', 'terraform', 'opentofu', 'pulumi', 'ansible'])
export type PluginIacEngine = z.infer<typeof pluginIacEngineSchema>

export const pluginConfigToolSchema = z.enum(['cloud-init', 'ansible', 'puppet', 'chef'])
export type PluginConfigTool = z.infer<typeof pluginConfigToolSchema>

export const pluginPhaseRunnerSchema = z.object({
  type: pluginRunnerTypeSchema,
  bundlePath: z.string().optional(),
  entry: z.string().optional(),
  playbookPath: z.string().optional(),
})
export type PluginPhaseRunner = z.infer<typeof pluginPhaseRunnerSchema>

export const pluginRunnersSchema = z.object({
  provision: pluginPhaseRunnerSchema.optional(),
  config: pluginPhaseRunnerSchema.optional(),
})
export type PluginRunners = z.infer<typeof pluginRunnersSchema>

export const pluginDefaultsSchema = z.object({
  iacEngine: pluginIacEngineSchema.optional(),
  configTool: pluginConfigToolSchema.optional(),
})
export type PluginDefaults = z.infer<typeof pluginDefaultsSchema>

export const pluginServiceTypeSchema = z.enum(['vm', 'container', 'kubernetes', 'paas'])
export type PluginServiceType = z.infer<typeof pluginServiceTypeSchema>

export const schemaFormatSchema = z.enum(['json', 'yaml'])
export type SchemaFormat = z.infer<typeof schemaFormatSchema>

const jsonSchemaType = z.enum([
  'object',
  'array',
  'string',
  'number',
  'integer',
  'boolean',
  'null',
])

/** JSON Schema document (Draft 7 subset) used for credentials + deploy config. */
export const jsonSchemaDocumentSchema: z.ZodType<Record<string, unknown>> = z
  .record(z.string(), z.unknown())
  .superRefine((value, ctx) => {
    if (Object.keys(value).length === 0) return
    const typeVal = value.type
    if (typeof typeVal === 'string' && !jsonSchemaType.safeParse(typeVal).success) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `not a valid JSON Schema: '${typeVal}' is not a valid type`,
      })
    }
    if (value.properties !== undefined && (typeof value.properties !== 'object' || value.properties === null || Array.isArray(value.properties))) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'not a valid JSON Schema: properties must be an object',
      })
    }
    if (value.required !== undefined && !Array.isArray(value.required)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'not a valid JSON Schema: required must be an array',
      })
    }
  })

export const pluginCapabilitiesSchema = z.object({
  serviceType: pluginServiceTypeSchema,
  supportsTtl: z.boolean(),
  supportsCustomDns: z.boolean(),
  supportsEphemeralDb: z.boolean(),
})
export type PluginCapabilities = z.infer<typeof pluginCapabilitiesSchema>

export const pluginRunnerSchema = z.object({
  type: pluginRunnerTypeSchema,
  bundlePath: z.string().optional(),
  entry: z.string().optional(),
})

export const pluginManifestPayloadSchema = z.object({
  id: z
    .string()
    .trim()
    .min(1, 'ID is required')
    .regex(/^[a-z][a-z0-9-]*$/, 'Use a kebab-case slug (e.g. digitalocean-droplet)'),
  label: z.string().trim().min(1, 'Label is required'),
  version: z
    .string()
    .trim()
    .regex(/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/, 'Must be a semantic version, e.g. 1.0.0'),
  category: pluginCategorySchema,
  description: z.string().trim().min(1, 'Description is required'),
  icon: z.string().trim().optional().default(''),
  runner: pluginRunnerSchema,
  capabilities: pluginCapabilitiesSchema,
  credentialsSchema: jsonSchemaDocumentSchema,
  deploymentConfigSchema: jsonSchemaDocumentSchema,
  runtime_targets: z.array(z.string()).optional(),
  docsUrl: z.string().trim().optional(),
  homepage: z.string().trim().optional(),
  license: z.string().trim().optional(),
  author: z.string().trim().optional(),
  keywords: z.array(z.string()).optional(),
  parentCloud: z.string().trim().optional(),
  runners: pluginRunnersSchema.optional(),
  defaults: pluginDefaultsSchema.optional(),
  credential_fields: z
    .array(
      z.object({
        name: z.string(),
        label: z.string(),
        secret: z.boolean().optional(),
        required: z.boolean().optional(),
        help: z.string().nullable().optional(),
      }),
    )
    .optional(),
})
export type PluginManifestPayload = z.infer<typeof pluginManifestPayloadSchema>

export interface PluginManifestForm {
  id: string
  label: string
  version: string
  category: PluginCategory
  description: string
  icon: string
  owner: 'user' | 'organization'
  visibility: 'private' | 'public'
  runnerType: PluginRunnerType
  runnerTarget: string
  /** When true, manifest stores separate provision + config runners (Option B stack). */
  useStackRunners: boolean
  provisionRunnerType: PluginRunnerType
  provisionRunnerTarget: string
  configRunnerType: PluginRunnerType
  configRunnerTarget: string
  defaultIacEngine: PluginIacEngine
  defaultConfigTool: PluginConfigTool
  serviceType: PluginServiceType
  supportsTtl: boolean
  supportsCustomDns: boolean
  supportsEphemeralDb: boolean
  credentialsSchema: Record<string, unknown>
  deploymentConfigSchema: Record<string, unknown>
  docsUrl: string
  homepage: string
  license: string
  author: string
  keywords: string[]
  parentCloud: string
}

export interface PluginFieldError {
  loc: string
  msg: string
}

export interface PluginValidateResponse {
  valid: boolean
  errors: PluginFieldError[]
  manifest: unknown
}

export const PLUGIN_IAC_ENGINES = pluginIacEngineSchema.options
export const PLUGIN_CONFIG_TOOLS = pluginConfigToolSchema.options
export const PLUGIN_CATEGORIES = pluginCategorySchema.options
export const PLUGIN_RUNNER_TYPES = pluginRunnerTypeSchema.options
export const PLUGIN_SERVICE_TYPES = pluginServiceTypeSchema.options
export const PLUGIN_PARENT_CLOUDS = [
  'gcp',
  'aws',
  'azure',
  'cloudflare',
  'hetzner',
  'digitalocean',
  'linode',
  'railway',
  'render',
] as const
