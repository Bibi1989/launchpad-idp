import { z } from 'zod'

export const projectStackSchema = z.enum([
  'node',
  'python',
  'go',
  'java',
  'rust',
  'unknown',
])

export const dockerfileSeveritySchema = z.enum(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'])

export const registryProviderSchema = z.enum([
  'docker_hub',
  'aws_ecr',
  'gcp_artifact_registry',
])

export const dockerfileSecurityIssueSchema = z.object({
  ruleId: z.string().min(1),
  severity: dockerfileSeveritySchema,
  description: z.string().min(1),
  lineNumber: z.number().int().positive().nullable().optional(),
})

export const dockerfileSecurityReportSchema = z.object({
  summary: z.string().min(1),
  securityIssues: z.array(dockerfileSecurityIssueSchema).default([]),
  hasMultiStage: z.boolean(),
  improvedDockerfile: z.string().min(1),
  explanationOfChanges: z.array(z.string()).default([]),
  analysisSource: z.enum(['gemini', 'heuristic']).optional(),
})

export const detectedDockerfileSchema = z.object({
  path: z.string(),
  content: z.string(),
  size_bytes: z.number().int().nonnegative(),
  sha: z.string().nullable().optional(),
})

export const dockerfileScanResponseSchema = z.object({
  full_name: z.string(),
  ref: z.string(),
  dockerfiles: z.array(detectedDockerfileSchema),
  detected_stack: projectStackSchema,
  scaffold_suggested: z.boolean(),
  root_markers: z.array(z.string()).default([]),
})

export const dockerfileScaffoldResponseSchema = z.object({
  stack: projectStackSchema,
  path: z.string(),
  content: z.string(),
  detected_from: z.array(z.string()).default([]),
})

export const dockerfileReviewResponseSchema = z.object({
  report: dockerfileSecurityReportSchema,
  source_path: z.string().nullable().optional(),
})

export const dockerfilePushResponseSchema = z.object({
  full_name: z.string(),
  html_url: z.string(),
  default_branch: z.string(),
  path: z.string(),
  commit_message: z.string(),
  installation_id: z.number().int(),
})

export const repoScaffoldFileSchema = z.object({
  path: z.string(),
  content: z.string(),
})

export const repoPushBundleResponseSchema = z.object({
  full_name: z.string(),
  html_url: z.string(),
  default_branch: z.string(),
  paths: z.array(z.string()),
  commit_message: z.string(),
  installation_id: z.number().int(),
})

export const dockerfileBuildJobStatusSchema = z.enum([
  'queued',
  'running',
  'succeeded',
  'failed',
])

export const dockerfileBuildEnqueueResponseSchema = z.object({
  job_id: z.string(),
  status: dockerfileBuildJobStatusSchema,
})

export const dockerfileBuildJobResponseSchema = z.object({
  job_id: z.string(),
  status: dockerfileBuildJobStatusSchema,
  image_refs: z.array(z.string()).default([]),
  logs: z.array(z.string()).default([]),
  error: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
})

export type ProjectStack = z.infer<typeof projectStackSchema>
export type DockerfileSeverity = z.infer<typeof dockerfileSeveritySchema>
export type RegistryProvider = z.infer<typeof registryProviderSchema>
export type DockerfileSecurityIssue = z.infer<typeof dockerfileSecurityIssueSchema>
export type DockerfileSecurityReport = z.infer<typeof dockerfileSecurityReportSchema>
export type DetectedDockerfile = z.infer<typeof detectedDockerfileSchema>
export type DockerfileScanResponse = z.infer<typeof dockerfileScanResponseSchema>
export type DockerfileScaffoldResponse = z.infer<typeof dockerfileScaffoldResponseSchema>
export type DockerfileReviewResponse = z.infer<typeof dockerfileReviewResponseSchema>
export type DockerfilePushResponse = z.infer<typeof dockerfilePushResponseSchema>
export type RepoPushBundleResponse = z.infer<typeof repoPushBundleResponseSchema>
export type DockerfileBuildJobStatus = z.infer<typeof dockerfileBuildJobStatusSchema>
export type DockerfileBuildEnqueueResponse = z.infer<typeof dockerfileBuildEnqueueResponseSchema>
export type DockerfileBuildJobResponse = z.infer<typeof dockerfileBuildJobResponseSchema>

export interface DockerHubCredentialsInput {
  username: string
  password_or_token: string
  repository: string
}

export interface AwsEcrCredentialsInput {
  access_key_id: string
  secret_access_key: string
  session_token?: string | null
  region: string
  account_id: string
  repository: string
}

export interface GcpArtifactRegistryCredentialsInput {
  service_account_json: string
  project_id: string
  location: string
  repository: string
  image_name: string
}

export interface RegistryTargetInput {
  provider: RegistryProvider
  docker_hub?: DockerHubCredentialsInput | null
  aws_ecr?: AwsEcrCredentialsInput | null
  gcp_artifact_registry?: GcpArtifactRegistryCredentialsInput | null
}

export interface DockerfileBuildPayload {
  installation_id: number
  full_name: string
  dockerfile_path?: string
  context_path?: string
  branch?: string | null
  tags: string[]
  registry: RegistryTargetInput
  dockerfile_content_override?: string | null
}

/**
 * Contract mirror of the Gemini structured-output schema used by the control plane.
 */
export const DockerfileSecurityReportSchemaContract = {
  type: 'OBJECT',
  properties: {
    summary: {
      type: 'STRING',
      description: 'Brief executive summary of Dockerfile security findings.',
    },
    securityIssues: {
      type: 'ARRAY',
      items: {
        type: 'OBJECT',
        properties: {
          ruleId: { type: 'STRING' },
          severity: { type: 'STRING', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
          description: { type: 'STRING' },
          lineNumber: { type: 'NUMBER' },
        },
      },
    },
    hasMultiStage: { type: 'BOOLEAN' },
    improvedDockerfile: { type: 'STRING' },
    explanationOfChanges: { type: 'ARRAY', items: { type: 'STRING' } },
  },
} as const
