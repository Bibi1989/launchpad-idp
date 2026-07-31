/** Shared helpers for sandbox commands with exit-code probes. */

export const LP_EXIT_MARKER_RE = /__LP_EXIT_CODE:(\d+)__/

export type TerminalCommandResult = {
  id: string
  command: string
  exitCode: number | null
  status: 'pending' | 'running' | 'ok' | 'error' | 'timeout'
  message: string
  finishedAt: number | null
}

export function wrapCommandWithExitProbe(command: string): string {
  // Run in a group so compound `cd …; terraform …` exit codes are captured.
  return [
    `{ ${command}; }`,
    '__lp_ec=$?',
    "printf '\\n__LP_EXIT_CODE:%s__\\n' \"$__lp_ec\"",
  ].join('; ')
}

export function parseExitCodeFromOutput(chunk: string): number | null {
  const match = chunk.match(LP_EXIT_MARKER_RE)
  if (!match?.[1]) return null
  return Number.parseInt(match[1], 10)
}

export function summarizeCommandFailure(command: string, exitCode: number, tail = ''): string {
  const shortCmd = command.length > 120 ? `${command.slice(0, 117)}…` : command
  const hint = tail.trim()
    ? `\n\nLast output:\n${tail.trim().slice(-800)}`
    : ''
  return `Step failed (exit ${exitCode}): ${shortCmd}${hint}`
}
