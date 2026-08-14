import { describe, expect, it } from 'vitest'
import { coerceRegionForProvider, isRegionForProvider } from '~/utils/cloudRegions'

describe('coerceRegionForProvider', () => {
  it('rejects GCP-style regions for AWS', () => {
    expect(isRegionForProvider('aws', 'us-central1')).toBe(false)
    expect(coerceRegionForProvider('aws', 'us-central1')).toBe('us-east-1')
    expect(coerceRegionForProvider('aws', 'us-central1', 'eu-central-1')).toBe('eu-central-1')
  })

  it('keeps valid AWS regions', () => {
    expect(coerceRegionForProvider('aws', 'eu-west-1')).toBe('eu-west-1')
  })
})
