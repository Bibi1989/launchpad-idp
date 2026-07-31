import { describe, expect, it } from 'vitest'
import {
  parseExitCodeFromOutput,
  summarizeCommandFailure,
  wrapCommandWithExitProbe,
} from '../app/utils/terminalCommandProbe'

describe('terminalCommandProbe', () => {
  it('wraps commands with an exit probe', () => {
    const wrapped = wrapCommandWithExitProbe('terraform init')
    expect(wrapped).toContain('{ terraform init; }')
    expect(wrapped).toContain('__LP_EXIT_CODE:')
  })

  it('parses exit codes from terminal output', () => {
    expect(parseExitCodeFromOutput('ok\n__LP_EXIT_CODE:0__\n')).toBe(0)
    expect(parseExitCodeFromOutput('fail\n__LP_EXIT_CODE:1__\nprompt$ ')).toBe(1)
    expect(parseExitCodeFromOutput('still running')).toBeNull()
  })

  it('summarizes failures for the wizard guardrail', () => {
    const msg = summarizeCommandFailure('terraform plan', 1, 'Error: no configuration')
    expect(msg).toContain('exit 1')
    expect(msg).toContain('Error: no configuration')
  })
})
