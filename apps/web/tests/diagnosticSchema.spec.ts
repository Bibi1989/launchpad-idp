import { describe, expect, it } from 'vitest'
import {
  DiagnosticReportSchemaContract,
  analyzePreviewResponseSchema,
  diagnosticReportSchema,
} from '~/types/diagnostic'

describe('diagnosticReportSchema', () => {
  it('accepts a valid Gemini structured report', () => {
    const parsed = diagnosticReportSchema.parse({
      summary: 'Trivy blocked the pipeline on CVE-2023-44487 in openssl.',
      category: 'CONTAINER_VULNERABILITY',
      severity: 'CRITICAL',
      securityDetails: {
        cveOrRuleId: 'CVE-2023-44487',
        affectedComponent: 'openssl@1.1.1t',
        recommendedUpgrade: 'Upgrade openssl to 1.1.1u',
      },
      rootCauseAnalysis: 'Critical HTTP/2 vulnerability in the container image.',
      actionableSteps: ['Upgrade package', 'Rebuild image'],
      patch: {
        targetFile: 'Dockerfile',
        originalContent: 'FROM node:18-alpine',
        suggestedContent: 'FROM node:20-alpine',
      },
      analysisSource: 'gemini',
    })
    expect(parsed.category).toBe('CONTAINER_VULNERABILITY')
    expect(parsed.patch?.targetFile).toBe('Dockerfile')
  })

  it('rejects unstructured dumps missing required fields', () => {
    const result = diagnosticReportSchema.safeParse({
      summary: 'something failed',
    })
    expect(result.success).toBe(false)
  })

  it('parses analyze API envelope', () => {
    const parsed = analyzePreviewResponseSchema.parse({
      report: {
        summary: 'CrashLoopBackOff on preview pod.',
        category: 'RUNTIME_CRASH',
        severity: 'HIGH',
        rootCauseAnalysis: 'Container exits immediately.',
        actionableSteps: ['Check kubectl logs'],
      },
      telemetrySummary: { trivyCount: 0, sastCount: 0 },
    })
    expect(parsed.report.category).toBe('RUNTIME_CRASH')
  })

  it('mirrors Gemini schema required keys', () => {
    expect(DiagnosticReportSchemaContract.required).toEqual([
      'summary',
      'category',
      'severity',
      'rootCauseAnalysis',
      'actionableSteps',
    ])
  })
})
