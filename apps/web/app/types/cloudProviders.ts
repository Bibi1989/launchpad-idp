import { z } from 'zod'

// Mirrors the backend catalog payload from GET /api/v1/cloud-providers
// (app/providers/registry.py build_catalog / catalog_for).

export const runtimeTargetSchema = z.enum(['vm', 'docker_host', 'kubernetes', 'paas'])
export type RuntimeTarget = z.infer<typeof runtimeTargetSchema>

export const credentialFieldSchema = z.object({
  name: z.string(),
  label: z.string(),
  secret: z.boolean().default(true),
  required: z.boolean().default(true),
  help: z.string().nullable().optional(),
  placeholder: z.string().nullable().optional(),
})
export type CredentialField = z.infer<typeof credentialFieldSchema>

export const regionOptionSchema = z.object({
  value: z.string(),
  label: z.string(),
})
export type RegionOption = z.infer<typeof regionOptionSchema>

export const computeTierSchema = z.object({
  id: z.string(),
  label: z.string(),
  vcpus: z.number().nullable().optional(),
  memory_mb: z.number().nullable().optional(),
  monthly_usd: z.number().nullable().optional(),
})
export type ComputeTier = z.infer<typeof computeTierSchema>

export const cloudServiceSchema = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string(),
})
export const cloudServiceGroupSchema = z.object({
  runtime: z.string(),
  label: z.string(),
  services: z.array(cloudServiceSchema),
})
export type CloudServiceGroup = z.infer<typeof cloudServiceGroupSchema>

export const cloudProviderCatalogEntrySchema = z.object({
  id: z.string(),
  label: z.string(),
  docs_url: z.string().nullable().optional(),
  runtime_targets: z.array(runtimeTargetSchema),
  credential_fields: z.array(credentialFieldSchema),
  regions: z.array(regionOptionSchema),
  tiers: z.array(computeTierSchema),
  services: z.array(cloudServiceGroupSchema).optional().default([]),
  source: z.string().optional(),
  category: z.string().nullable().optional(),
  icon: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  parent_cloud: z.string().nullable().optional(),
  service: z.string().nullable().optional(),
  owner: z.enum(['user', 'organization']).optional(),
  visibility: z.enum(['private', 'public']).optional(),
  can_edit: z.boolean().optional(),
  defaults: z
    .object({
      iacEngine: z.string().optional(),
      configTool: z.string().optional(),
    })
    .passthrough()
    .optional(),
  runners: z.record(z.string(), z.unknown()).optional(),
})
export type CloudProviderCatalogEntry = z.infer<typeof cloudProviderCatalogEntrySchema>

export const cloudProviderCatalogSchema = z.array(cloudProviderCatalogEntrySchema)
export type CloudProviderCatalog = z.infer<typeof cloudProviderCatalogSchema>

// Selection the picker emits back to the wizard.
export interface CloudProviderSelection {
  provider: string
  region: string | null
  tier: string | null
  credentials: Record<string, string>
}

// Provisioning + configuration tool catalog (GET /api/v1/provisioning-tools).
export const provisioningToolSchema = z.object({
  id: z.string(),
  label: z.string(),
  category: z.enum(['iac', 'config']),
  description: z.string(),
  supported_clouds: z.array(z.string()),
  docs_url: z.string().nullable().optional(),
  implemented: z.boolean().default(true),
  default: z.boolean().default(false),
})
export type ProvisioningTool = z.infer<typeof provisioningToolSchema>

export const provisioningToolsSchema = z.array(provisioningToolSchema)

// Provisioning preview / scaffold (files written into the workspace).
export const scaffoldFileSchema = z.object({
  path: z.string(),
  content: z.string(),
})
export type ScaffoldFile = z.infer<typeof scaffoldFileSchema>
export const scaffoldFilesSchema = z.array(scaffoldFileSchema)

export interface ProvisioningSpecInput {
  name?: string | null
  image?: string | null
  app_port?: number
  region?: string | null
  tier?: string | null
  runtime_target?: string
  env_vars?: Record<string, string>
}

// Credential status: which fields are configured per provider (never the values).
export const providerCredentialsStatusSchema = z.record(z.string(), z.array(z.string()))
export type ProviderCredentialsStatus = z.infer<typeof providerCredentialsStatusSchema>

export const providerValidateResponseSchema = z.object({
  valid: z.boolean(),
  message: z.string().nullable().optional(),
})
export type ProviderValidateResponse = z.infer<typeof providerValidateResponseSchema>

export const ALL_CLOUDS = '*'
