/** Align with API Settings: default_ttl_hours / ttl_max_total_hours_from_create. */
export const PREVIEW_TTL_DEFAULT_HOURS = 2
export const PREVIEW_TTL_MAX_HOURS = 168
export const PREVIEW_TTL_MAX_MINUTES = PREVIEW_TTL_MAX_HOURS * 60

export const PREVIEW_TTL_MAX_MS = PREVIEW_TTL_MAX_HOURS * 60 * 60 * 1000

export function ttlLeftMs(expiresAt: string | null | undefined, now = Date.now()): number {
  if (!expiresAt) return Number.POSITIVE_INFINITY
  const expires = new Date(expiresAt).getTime()
  if (!Number.isFinite(expires)) return 0
  return Math.max(expires - now, 0)
}

export function ttlLeftSeconds(expiresAt: string | null | undefined, now = Date.now()): number {
  const ms = ttlLeftMs(expiresAt, now)
  if (!Number.isFinite(ms)) return Number.POSITIVE_INFINITY
  return Math.floor(ms / 1000)
}

/** Progress fill against the governance max TTL window (not expires-created). */
export function ttlProgressRatio(expiresAt: string | null | undefined, now = Date.now()): number {
  if (!expiresAt) return 1
  const left = ttlLeftMs(expiresAt, now)
  return Math.min(Math.max(left / PREVIEW_TTL_MAX_MS, 0), 1)
}

export function ttlIsExpired(expiresAt: string | null | undefined, now = Date.now()): boolean {
  if (!expiresAt) return false
  return ttlLeftMs(expiresAt, now) <= 0
}

/** True when extend can still push expires toward max-from-create. */
export function ttlCanExtend(
  createdAt: string,
  expiresAt: string | null | undefined,
  now = Date.now(),
  skewMs = 30_000,
): boolean {
  if (!expiresAt) return false
  const created = new Date(createdAt).getTime()
  const expires = new Date(expiresAt).getTime()
  if (!Number.isFinite(created) || !Number.isFinite(expires)) return false
  if (ttlIsExpired(expiresAt, now)) return false
  const maxExpiry = created + PREVIEW_TTL_MAX_MS
  return expires + skewMs < maxExpiry
}
