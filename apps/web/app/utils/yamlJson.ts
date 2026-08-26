/**
 * JSON-compatible YAML dump/parse for the plugin schema editors.
 * Covers mappings, sequences, and scalars (the subset JSON Schema documents use).
 */

export interface YamlParseError {
  line: number
  message: string
}

export class StructuredParseError extends Error {
  readonly line: number

  constructor(message: string, line = 1) {
    super(message)
    this.name = 'StructuredParseError'
    this.line = line
  }
}

function yamlQuote(value: string): string {
  if (value === '') return "''"
  if (/^[-?:,\[\]{}#&*!|>'"%@`~]|[\n:]|\s/.test(value) || /^(true|false|null|~)$/i.test(value)) {
    return JSON.stringify(value)
  }
  return value
}

function yamlKey(key: string): string {
  return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(key) ? key : JSON.stringify(key)
}

export function dumpYaml(value: unknown, indent = 0): string {
  const pad = '  '.repeat(indent)
  if (value === null || value === undefined) return `${pad}null`
  if (typeof value === 'boolean') return `${pad}${value ? 'true' : 'false'}`
  if (typeof value === 'number') return `${pad}${Number.isFinite(value) ? String(value) : 'null'}`
  if (typeof value === 'string') return `${pad}${yamlQuote(value)}`
  if (Array.isArray(value)) {
    if (value.length === 0) return `${pad}[]`
    return value
      .map((item) => {
        if (item && typeof item === 'object' && !Array.isArray(item)) {
          const dumped = dumpYaml(item, indent + 1)
          const lines = dumped.split('\n')
          const first = lines[0]?.replace(/^\s+/, '') ?? ''
          const rest = lines.slice(1).join('\n')
          return rest ? `${pad}- ${first}\n${rest}` : `${pad}- ${first}`
        }
        if (Array.isArray(item) && item.length > 0) {
          const dumped = dumpYaml(item, indent + 1)
          return `${pad}-\n${dumped}`
        }
        return `${pad}- ${dumpYaml(item, 0).trimStart()}`
      })
      .join('\n')
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return `${pad}{}`
    return entries
      .map(([key, nested]) => {
        const label = yamlKey(key)
        if (nested && typeof nested === 'object') {
          const isEmpty = Array.isArray(nested)
            ? nested.length === 0
            : Object.keys(nested as object).length === 0
          if (isEmpty) {
            return `${pad}${label}: ${Array.isArray(nested) ? '[]' : '{}'}`
          }
          return `${pad}${label}:\n${dumpYaml(nested, indent + 1)}`
        }
        return `${pad}${label}: ${dumpYaml(nested, 0).trimStart()}`
      })
      .join('\n')
  }
  return `${pad}${yamlQuote(String(value))}`
}

export function dumpJson(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`
}

function parseScalar(raw: string): unknown {
  const text = raw.trim()
  if (text === '' || text === '~' || text === 'null' || text === 'Null') return null
  if (text === 'true' || text === 'True') return true
  if (text === 'false' || text === 'False') return false
  if (/^-?\d+$/.test(text)) return Number.parseInt(text, 10)
  if (/^-?\d+\.\d+$/.test(text)) return Number.parseFloat(text)
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    try {
      if (text.startsWith('"')) return JSON.parse(text) as string
      return text.slice(1, -1)
    } catch {
      return text.slice(1, -1)
    }
  }
  return text
}

interface Line {
  indent: number
  content: string
  number: number
}

function tokenize(text: string): Line[] {
  const out: Line[] = []
  const rows = text.split('\n')
  for (let i = 0; i < rows.length; i += 1) {
    const raw = rows[i] ?? ''
    const trimmed = raw.replace(/\s+#.*$/, '')
    if (!trimmed.trim() || trimmed.trim().startsWith('#')) continue
    const indent = raw.length - raw.trimStart().length
    out.push({ indent, content: trimmed.trim(), number: i + 1 })
  }
  return out
}

function parseBlock(lines: Line[], index: number, indent: number): { value: unknown; next: number } {
  if (index >= lines.length) return { value: null, next: index }
  const first = lines[index]
  if (!first || first.indent < indent) return { value: null, next: index }

  if (first.content.startsWith('- ')) {
    return parseSequence(lines, index, first.indent)
  }
  if (first.content.includes(':')) {
    return parseMapping(lines, index, first.indent)
  }
  return { value: parseScalar(first.content), next: index + 1 }
}

function parseSequence(lines: Line[], index: number, indent: number): { value: unknown; next: number } {
  const items: unknown[] = []
  let i = index
  while (i < lines.length) {
    const line = lines[i]
    if (!line || line.indent < indent) break
    if (line.indent > indent) {
      throw new StructuredParseError(`Unexpected indent at line ${line.number}`, line.number)
    }
    if (!line.content.startsWith('- ')) {
      throw new StructuredParseError(`Expected list item at line ${line.number}`, line.number)
    }
    const rest = line.content.slice(2).trim()
    if (!rest) {
      const nested = parseBlock(lines, i + 1, indent + 1)
      items.push(nested.value)
      i = nested.next
      continue
    }
    if (rest.includes(':') && !rest.startsWith('{') && !rest.startsWith('[')) {
      const inline = parseMappingFromHeader(rest, lines, i + 1, indent + 2, line.number)
      items.push(inline.value)
      i = inline.next
      continue
    }
    items.push(parseScalar(rest))
    i += 1
  }
  return { value: items, next: i }
}

function parseMapping(
  lines: Line[],
  index: number,
  indent: number,
): { value: Record<string, unknown>; next: number } {
  const obj: Record<string, unknown> = {}
  let i = index
  while (i < lines.length) {
    const line = lines[i]
    if (!line || line.indent < indent) break
    if (line.indent > indent) {
      throw new StructuredParseError(`Unexpected indent at line ${line.number}`, line.number)
    }
    if (line.content.startsWith('- ')) {
      throw new StructuredParseError(`Unexpected list item at line ${line.number}`, line.number)
    }
    const parsed = parseMappingEntry(line.content, lines, i + 1, indent + 2, line.number)
    obj[parsed.key] = parsed.value
    i = parsed.next
  }
  return { value: obj, next: i }
}

function parseMappingFromHeader(
  header: string,
  lines: Line[],
  nextIndex: number,
  childIndent: number,
  lineNumber: number,
): { value: Record<string, unknown>; next: number } {
  const parsed = parseMappingEntry(header, lines, nextIndex, childIndent, lineNumber)
  const obj: Record<string, unknown> = { [parsed.key]: parsed.value }
  let i = parsed.next
  while (i < lines.length) {
    const line = lines[i]
    if (!line || line.indent < childIndent - 2) break
    if (line.content.startsWith('- ')) break
    if (line.indent !== childIndent - 2 && line.indent !== childIndent) break
    if (line.indent === childIndent - 2 && line.content.includes(':')) {
      const extra = parseMappingEntry(line.content, lines, i + 1, childIndent, line.number)
      obj[extra.key] = extra.value
      i = extra.next
      continue
    }
    break
  }
  return { value: obj, next: i }
}

function parseMappingEntry(
  content: string,
  lines: Line[],
  nextIndex: number,
  childIndent: number,
  lineNumber: number,
): { key: string; value: unknown; next: number } {
  const colon = content.indexOf(':')
  if (colon < 0) {
    throw new StructuredParseError(`Expected key: value at line ${lineNumber}`, lineNumber)
  }
  const keyRaw = content.slice(0, colon).trim()
  const key = keyRaw.startsWith('"') ? String(parseScalar(keyRaw)) : keyRaw
  const rest = content.slice(colon + 1).trim()
  if (!rest) {
    if (nextIndex < lines.length && (lines[nextIndex]?.indent ?? -1) >= childIndent) {
      const nested = parseBlock(lines, nextIndex, childIndent)
      return { key, value: nested.value, next: nested.next }
    }
    return { key, value: null, next: nextIndex }
  }
  if (rest === '{}' || rest === '[]') {
    return { key, value: rest === '{}' ? {} : [], next: nextIndex }
  }
  if (rest.startsWith('{') || rest.startsWith('[')) {
    try {
      return { key, value: JSON.parse(rest) as unknown, next: nextIndex }
    } catch {
      throw new StructuredParseError(`Invalid inline JSON at line ${lineNumber}`, lineNumber)
    }
  }
  return { key, value: parseScalar(rest), next: nextIndex }
}

export function parseYaml(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return {}
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    return JSON.parse(trimmed) as unknown
  }
  const lines = tokenize(text)
  if (lines.length === 0) return {}
  const { value } = parseBlock(lines, 0, lines[0]?.indent ?? 0)
  return value
}

export function parseJson(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return {}
  return JSON.parse(trimmed) as unknown
}

function lineFromJsonPosition(text: string, position: number): number {
  if (position < 0) return 1
  return text.slice(0, position).split('\n').length
}

export function parseStructured(
  text: string,
  format: 'json' | 'yaml',
): { value: Record<string, unknown> | null; error: YamlParseError | null } {
  try {
    const parsed = format === 'yaml' ? parseYaml(text) : parseJson(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { value: null, error: { line: 1, message: 'Document must be a JSON object' } }
    }
    return { value: parsed as Record<string, unknown>, error: null }
  } catch (err) {
    if (err instanceof StructuredParseError) {
      return { value: null, error: { line: err.line, message: err.message } }
    }
    const message = err instanceof Error ? err.message : 'Invalid document'
    const match = /position\s+(\d+)/i.exec(message)
    const line = match ? lineFromJsonPosition(text, Number(match[1])) : 1
    return { value: null, error: { line, message } }
  }
}

export function dumpStructured(value: unknown, format: 'json' | 'yaml'): string {
  return format === 'yaml' ? `${dumpYaml(value)}\n` : dumpJson(value)
}

export function detectStructuredFormat(text: string): 'json' | 'yaml' | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) return 'json'
  const jsonAttempt = parseStructured(text, 'json')
  if (!jsonAttempt.error) return 'json'
  const yamlAttempt = parseStructured(text, 'yaml')
  if (!yamlAttempt.error) return 'yaml'
  if (/^[\w"'[][^\n]*:/m.test(trimmed) || trimmed.includes('\n- ')) return 'yaml'
  return null
}
