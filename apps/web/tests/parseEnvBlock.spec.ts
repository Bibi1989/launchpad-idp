import { describe, it, expect } from 'vitest'
import { parseEnvBlock } from '../app/utils/parseEnvBlock'

describe('parseEnvBlock', () => {
  it('parses KEY=VALUE lines', () => {
    expect(parseEnvBlock('FOO=bar\nBAZ=qux')).toEqual([
      { key: 'FOO', value: 'bar' },
      { key: 'BAZ', value: 'qux' },
    ])
  })

  it('skips blanks and comments, strips export + surrounding quotes', () => {
    const block = `
# a comment
export TOKEN="s3cr3t"
NAME = 'Ada Lovelace'

EMPTY=
`
    expect(parseEnvBlock(block)).toEqual([
      { key: 'TOKEN', value: 's3cr3t' },
      { key: 'NAME', value: 'Ada Lovelace' },
      { key: 'EMPTY', value: '' },
    ])
  })

  it('keeps = and : inside values (URLs, base64) intact', () => {
    expect(parseEnvBlock('DATABASE_URL=postgres://u:p@host:5432/db?x=1')).toEqual([
      { key: 'DATABASE_URL', value: 'postgres://u:p@host:5432/db?x=1' },
    ])
  })

  it('skips lines without = or with whitespace in the key', () => {
    expect(parseEnvBlock('NOT_AN_ASSIGNMENT\nbad key=value\nGOOD=1')).toEqual([
      { key: 'GOOD', value: '1' },
    ])
  })

  it('last duplicate wins', () => {
    expect(parseEnvBlock('K=1\nK=2')).toEqual([{ key: 'K', value: '2' }])
  })

  it('returns [] for empty input', () => {
    expect(parseEnvBlock('')).toEqual([])
    expect(parseEnvBlock('\n\n')).toEqual([])
  })
})
