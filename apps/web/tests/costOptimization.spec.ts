import { describe, expect, it } from 'vitest'
import {
  applyCostOptimizationToWorkloadOptions,
  applyResourcePreset,
  costOptimizationFromApi,
  costOptimizationToApi,
  defaultCostOptimizationConfig,
} from '../app/utils/costOptimization'
import { defaultKubernetesWorkloadOptions } from '../app/utils/cloudValidation'

describe('costOptimization', () => {
  it('applies resource presets', () => {
    const base = defaultCostOptimizationConfig().resources
    expect(applyResourcePreset('balanced', base)).toMatchObject({
      preset: 'balanced',
      cpuRequest: '250m',
      memoryRequest: '512Mi',
    })
    expect(applyResourcePreset('performance', base).cpuRequest).toBe('1')
  })

  it('syncs HPA/VPA workload options', () => {
    const options = defaultKubernetesWorkloadOptions()
    const cost = defaultCostOptimizationConfig()
    cost.hpa.enabled = true
    cost.vpa.enabled = true
    const next = applyCostOptimizationToWorkloadOptions(options, cost)
    expect(next.hpa).toBe(true)
    expect(next.vpa).toBe(true)
  })

  it('round-trips API snake_case', () => {
    const cost = defaultCostOptimizationConfig()
    cost.spotScheduling.enabled = true
    cost.spotScheduling.allocationPercent = 40
    cost.hpa.enabled = true
    cost.hpa.minReplicas = 3
    const api = costOptimizationToApi(cost)
    const back = costOptimizationFromApi(api)
    expect(back.spotScheduling.enabled).toBe(true)
    expect(back.spotScheduling.allocationPercent).toBe(40)
    expect(back.hpa.minReplicas).toBe(3)
  })
})
