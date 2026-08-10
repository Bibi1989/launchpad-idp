import { z } from 'zod'

export const diagnosticCategorySchema = z.enum([
  'CONTAINER_VULNERABILITY',
  'SAST_CODE_SECURITY',
  'RUNTIME_CRASH',
  'CONFIGURATION_ERROR',
])

export const diagnosticSeveritySchema = z.enum(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'])

export const securityDetailsSchema = z.object({
  cveOrRuleId: z.string().min(1),
  affectedComponent: z.string().min(1),
  recommendedUpgrade: z.string().min(1),
})

export const diagnosticPatchSchema = z.object({
  targetFile: z.string().min(1),
  originalContent: z.string(),
  suggestedContent: z.string(),
})

export const diagnosticReportSchema = z.object({
  summary: z.string().min(1),
  category: diagnosticCategorySchema,
  severity: diagnosticSeveritySchema,
  securityDetails: securityDetailsSchema.nullable().optional(),
  rootCauseAnalysis: z.string().min(1),
  actionableSteps: z.array(z.string().min(1)).min(1),
  patch: diagnosticPatchSchema.nullable().optional(),
  analysisSource: z.enum(['gemini', 'heuristic']).optional(),
})

export const analyzePreviewResponseSchema = z.object({
  report: diagnosticReportSchema,
  telemetrySummary: z.record(z.unknown()).default({}),
  geminiConfigured: z.boolean().optional().default(false),
})

export type DiagnosticCategory = z.infer<typeof diagnosticCategorySchema>
export type DiagnosticSeverity = z.infer<typeof diagnosticSeveritySchema>
export type SecurityDetails = z.infer<typeof securityDetailsSchema>
export type DiagnosticPatch = z.infer<typeof diagnosticPatchSchema>
export type DiagnosticReport = z.infer<typeof diagnosticReportSchema>
export type AnalyzePreviewResponse = z.infer<typeof analyzePreviewResponseSchema>

export interface AnalyzePreviewPayload {
  cicdLogs?: string | null
  kubernetesLogs?: string | null
  trivySarif?: Record<string, unknown> | string | null
  codeqlSarif?: Record<string, unknown> | string | null
  sastLogs?: string | null
  manifestSnippets?: Record<string, string> | null
  includeEnvironmentLogs?: boolean
}

/**
 * Contract mirror of the Gemini structured-output schema used by the control plane.
 * Property shapes match `@google/genai` Type.OBJECT definitions (see API DIAGNOSTIC_REPORT_JSON_SCHEMA).
 */
export const DiagnosticReportSchemaContract = {
  type: 'OBJECT',
  properties: {
    summary: {
      type: 'STRING',
      description: 'Concise 2-sentence summary explaining why the pipeline or deployment failed.',
    },
    category: {
      type: 'STRING',
      enum: [
        'CONTAINER_VULNERABILITY',
        'SAST_CODE_SECURITY',
        'RUNTIME_CRASH',
        'CONFIGURATION_ERROR',
      ],
      description: 'High-level classification of the failure.',
    },
    severity: {
      type: 'STRING',
      enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'],
      description: 'Overall impact severity level.',
    },
    securityDetails: {
      type: 'OBJECT',
      properties: {
        cveOrRuleId: { type: 'STRING', description: 'e.g., CVE-2024-1234 or js/sql-injection' },
        affectedComponent: { type: 'STRING', description: 'Vulnerable package or file line' },
        recommendedUpgrade: { type: 'STRING', description: 'Remediation target version or fix' },
      },
      description:
        'Security-specific findings if category is CONTAINER_VULNERABILITY or SAST_CODE_SECURITY.',
    },
    rootCauseAnalysis: {
      type: 'STRING',
      description: 'In-depth technical breakdown of the root cause.',
    },
    actionableSteps: {
      type: 'ARRAY',
      items: { type: 'STRING' },
      description: 'Ordered list of resolution steps for the user.',
    },
    patch: {
      type: 'OBJECT',
      properties: {
        targetFile: {
          type: 'STRING',
          description: 'Path to file needing updates (e.g. Dockerfile, infra/values.yaml)',
        },
        originalContent: { type: 'STRING', description: 'Original snippet or block' },
        suggestedContent: { type: 'STRING', description: 'Corrected snippet or block' },
      },
      description: 'Specific file patch to fix the issue.',
    },
  },
  required: ['summary', 'category', 'severity', 'rootCauseAnalysis', 'actionableSteps'],
} as const
