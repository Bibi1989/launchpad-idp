export interface ParsedEnvVar {
  key: string
  value: string
}

/**
 * Parse a pasted `.env`-style block into key/value pairs.
 *
 * Tolerant of the formats people actually paste:
 * - `KEY=value`, `KEY = value`, `export KEY=value`
 * - surrounding single or double quotes around the value (stripped)
 * - blank lines and `#` comment lines (skipped)
 * - values containing `=` (only the first `=` splits key from value, so URLs and
 *   base64 secrets survive intact)
 *
 * Keys are trimmed; a key with whitespace or no `=` on the line is skipped. Later
 * duplicates win (last value for a key).
 */
export function parseEnvBlock(input: string): ParsedEnvVar[] {
  const byKey = new Map<string, string>()
  if (!input) return []

  for (const rawLine of input.split(/\r?\n/)) {
    let line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    if (line.startsWith('export ')) line = line.slice('export '.length).trim()

    const eq = line.indexOf('=')
    if (eq === -1) continue

    const key = line.slice(0, eq).trim()
    if (!key || /\s/.test(key)) continue

    let value = line.slice(eq + 1).trim()
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1)
    }

    byKey.set(key, value)
  }

  return Array.from(byKey, ([key, value]) => ({ key, value }))
}
