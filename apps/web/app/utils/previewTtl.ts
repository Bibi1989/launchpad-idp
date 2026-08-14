/** Align with API Settings: default_ttl_hours / ttl_max_total_hours_from_create. */
export const PREVIEW_TTL_DEFAULT_HOURS = 2
export const PREVIEW_TTL_MAX_HOURS = 168
export const PREVIEW_TTL_MAX_MINUTES = PREVIEW_TTL_MAX_HOURS * 60

export const PREVIEW_TTL_MAX_MS = PREVIEW_TTL_MAX_HOURS * 60 * 60 * 1000

export function ttlLeftMs(expiresAt: string, now = Date.now()): number {
  const expires = new Date(expiresAt).getTime()
  if (!Number.isFinite(expires)) return 0
  return Math.max(expires - now, 0)
}

export function ttlLeftSeconds(expiresAt: string, now = Date.now()): number {
  return Math.floor(ttlLeftMs(expiresAt, now) / 1000)
}

/** Progress fill against the governance max TTL window (not expires-created). */
export function ttlProgressRatio(expiresAt: string, now = Date.now()): number {
  const left = ttlLeftMs(expiresAt, now)
  return Math.min(Math.max(left / PREVIEW_TTL_MAX_MS, 0), 1)
}

export function ttlIsExpired(expiresAt: string, now = Date.now()): boolean {
  return ttlLeftMs(expiresAt, now) <= 0
}

/** True when extend can still push expires toward max-from-create. */
export function ttlCanExtend(
  createdAt: string,
  expiresAt: string,
  now = Date.now(),
  skewMs = 30_000,
): boolean {
  const created = new Date(createdAt).getTime()
  const expires = new Date(expiresAt).getTime()
  if (!Number.isFinite(created) || !Number.isFinite(expires)) return false
  if (ttlIsExpired(expiresAt, now)) return false
  const maxExpiry = created + PREVIEW_TTL_MAX_MS
  return expires + skewMs < maxExpiry
}
