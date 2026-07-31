import {
  parseExitCodeFromOutput,
  summarizeCommandFailure,
  wrapCommandWithExitProbe,
  type TerminalCommandResult,
} from '~/utils/terminalCommandProbe'

const DEFAULT_TIMEOUT_MS = 900_000

/**
 * Queue a shell command in the sandbox terminal and wait for an exit-code probe.
 * TerminalPanel must be mounted and connected for this to resolve.
 */
export function useGuardedTerminalCommand() {
  const commandQueue = useState<string[]>('lp-terminal-cmd-queue', () => [])
  const result = useState<TerminalCommandResult | null>('lp-terminal-cmd-result', () => null)
  const outputBuffer = useState<string>('lp-terminal-output-buffer', () => '')
  const terminalConnected = useState<boolean>('lp-terminal-connected', () => false)

  function resetResult() {
    result.value = null
    outputBuffer.value = ''
  }

  async function waitForTerminal(timeoutMs = 45_000): Promise<boolean> {
    if (terminalConnected.value) return true
    const started = Date.now()
    while (Date.now() - started < timeoutMs) {
      if (terminalConnected.value) return true
      await new Promise((r) => setTimeout(r, 200))
    }
    return terminalConnected.value
  }

  async function runGuarded(
    command: string,
    opts: { timeoutMs?: number } = {},
  ): Promise<TerminalCommandResult> {
    const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS
    const id = crypto.randomUUID()
    resetResult()
    result.value = {
      id,
      command,
      exitCode: null,
      status: 'pending',
      message: 'Waiting for sandbox…',
      finishedAt: null,
    }

    const ready = await waitForTerminal()
    if (!ready) {
      const failed: TerminalCommandResult = {
        id,
        command,
        exitCode: null,
        status: 'error',
        message: 'Sandbox terminal did not connect. Open or reconnect the terminal and retry.',
        finishedAt: Date.now(),
      }
      result.value = failed
      return failed
    }

    result.value = {
      ...result.value,
      status: 'running',
      message: 'Running in sandbox…',
    }
    outputBuffer.value = ''
    commandQueue.value = [...commandQueue.value, wrapCommandWithExitProbe(command)]

    const started = Date.now()
    while (Date.now() - started < timeoutMs) {
      const current = result.value
      if (current?.id === id && (current.status === 'ok' || current.status === 'error')) {
        return current
      }
      // Also resolve if marker was parsed into a finished result with matching id
      if (
        current?.id === id
        && current.exitCode !== null
        && (current.status === 'ok' || current.status === 'error')
      ) {
        return current
      }
      await new Promise((r) => setTimeout(r, 250))
    }

    const timedOut: TerminalCommandResult = {
      id,
      command,
      exitCode: null,
      status: 'timeout',
      message: `Timed out after ${Math.round(timeoutMs / 1000)}s waiting for step to finish.`,
      finishedAt: Date.now(),
    }
    result.value = timedOut
    return timedOut
  }

  /** Called by TerminalPanel when PTY output arrives. */
  function ingestTerminalOutput(chunk: string) {
    if (!result.value || result.value.status !== 'running') {
      // Still buffer lightly in case marker arrives slightly late
      if (result.value?.status === 'pending') {
        outputBuffer.value = `${outputBuffer.value}${chunk}`.slice(-12_000)
      }
      return
    }
    outputBuffer.value = `${outputBuffer.value}${chunk}`.slice(-12_000)
    const code = parseExitCodeFromOutput(outputBuffer.value)
    if (code === null) return

    const ok = code === 0
    result.value = {
      ...result.value,
      exitCode: code,
      status: ok ? 'ok' : 'error',
      message: ok
        ? `Completed successfully (exit ${code})`
        : summarizeCommandFailure(result.value.command, code, outputBuffer.value),
      finishedAt: Date.now(),
    }
  }

  function setConnected(value: boolean) {
    terminalConnected.value = value
  }

  return {
    result,
    terminalConnected,
    runGuarded,
    ingestTerminalOutput,
    setConnected,
    waitForTerminal,
    resetResult,
  }
}
