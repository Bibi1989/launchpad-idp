/**
 * Small presentational formatters shared across environment views.
 * Extracted from EnvironmentCard / environment pages / NotificationBell, which
 * each had their own copy of this logic.
 */

const COST_SOURCE_LABELS: Record<string, string> = {
  usage_quota: 'quota usage',
  usage_requests: 'pod requests',
  estimate: 'estimate',
  idle: 'idle',
}

/** Human label for an environment cost_source, or null when there is none. */
export function formatCostSource(source: string | null | undefined): string | null {
  if (!source) return null
  return COST_SOURCE_LABELS[source] ?? source
}

/** Format API cost amounts (already in display currency, default EUR). */
export function formatCostAmount(
  value: string | number | null | undefined,
  opts: { decimals?: number } = {},
): string {
  const decimals = opts.decimals ?? 2
  const n = Number.parseFloat(String(value ?? '0'))
  if (!Number.isFinite(n)) return (0).toFixed(decimals)
  return n.toFixed(decimals)
}

export const COST_DISPLAY_SYMBOL = '€'

export interface FormatDurationOptions {
  /** Zero-pad hours/minutes to two digits (e.g. "02h 05m"). */
  pad?: boolean
  /** Label returned when there is no time left. Defaults to "Expired". */
  expiredLabel?: string
}

/** Format a remaining-seconds count as "Hh Mm" (or the expired label at <= 0). */
export function formatDuration(seconds: number, opts: FormatDurationOptions = {}): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return opts.expiredLabel ?? 'Expired'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (opts.pad) {
    return `${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}m`
  }
  return `${hours}h ${minutes}m`
}

/** Compact "time ago" label from an epoch-millisecond timestamp. */
export function formatRelativeTime(ts: number, now: number = Date.now()): string {
  const diff = now - ts
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}
