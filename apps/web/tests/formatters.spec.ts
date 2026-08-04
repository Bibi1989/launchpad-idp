import { describe, expect, it } from 'vitest'
import { formatCostSource, formatDuration, formatRelativeTime } from '../app/utils/formatters'

describe('formatCostSource', () => {
  it('maps known sources to friendly labels', () => {
    expect(formatCostSource('usage_quota')).toBe('quota usage')
    expect(formatCostSource('usage_requests')).toBe('pod requests')
    expect(formatCostSource('estimate')).toBe('estimate')
    expect(formatCostSource('idle')).toBe('idle')
  })

  it('passes through unknown sources and returns null for empty', () => {
    expect(formatCostSource('spot_market')).toBe('spot_market')
    expect(formatCostSource(null)).toBeNull()
    expect(formatCostSource(undefined)).toBeNull()
    expect(formatCostSource('')).toBeNull()
  })
})

describe('formatDuration', () => {
  it('formats hours and minutes', () => {
    expect(formatDuration(3 * 3600 + 25 * 60)).toBe('3h 25m')
    expect(formatDuration(59)).toBe('0h 0m')
  })

  it('zero-pads when asked', () => {
    expect(formatDuration(2 * 3600 + 5 * 60, { pad: true })).toBe('02h 05m')
  })

  it('returns the expired label at or below zero and for non-finite input', () => {
    expect(formatDuration(0)).toBe('Expired')
    expect(formatDuration(-10)).toBe('Expired')
    expect(formatDuration(Number.NaN)).toBe('Expired')
    expect(formatDuration(0, { expiredLabel: '-' })).toBe('-')
  })
})

describe('formatRelativeTime', () => {
  const now = 1_000_000_000_000

  it('bins the delta into just-now / minutes / hours / days', () => {
    expect(formatRelativeTime(now, now)).toBe('just now')
    expect(formatRelativeTime(now - 30_000, now)).toBe('just now')
    expect(formatRelativeTime(now - 5 * 60_000, now)).toBe('5m ago')
    expect(formatRelativeTime(now - 3 * 3_600_000, now)).toBe('3h ago')
    expect(formatRelativeTime(now - 2 * 86_400_000, now)).toBe('2d ago')
  })
})
