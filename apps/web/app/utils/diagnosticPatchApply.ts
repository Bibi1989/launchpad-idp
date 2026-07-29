import type { DiagnosticPatch } from '~/types/diagnostic'

export type PatchApplyMode = 'replaced' | 'created' | 'appended'

export interface PatchApplyPlan {
  nextContent: string
  mode: PatchApplyMode
}

/**
 * Build the file content that results from applying an analyzer patch.
 * - If current content contains originalContent → replace once
 * - If no current content → create with suggestedContent
 * - Otherwise append suggestedContent with a marker (avoid silent whole-file overwrite)
 */
export function planDiagnosticPatchApply(
  currentContent: string | null,
  patch: DiagnosticPatch,
): PatchApplyPlan {
  const original = patch.originalContent
  const suggested = patch.suggestedContent

  if (currentContent == null || currentContent === '') {
    return { nextContent: suggested, mode: 'created' }
  }

  if (original && currentContent.includes(original)) {
    return {
      nextContent: currentContent.replace(original, suggested),
      mode: 'replaced',
    }
  }

  const marker = `# launchpad-analyzer-fix: ${patch.targetFile}`
  if (currentContent.includes(marker)) {
    return { nextContent: currentContent, mode: 'appended' }
  }

  const separator = currentContent.endsWith('\n') ? '\n' : '\n\n'
  return {
    nextContent: `${currentContent.trimEnd()}${separator}${marker}\n${suggested}\n`,
    mode: 'appended',
  }
}
