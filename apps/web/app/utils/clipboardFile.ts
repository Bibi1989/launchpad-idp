/** Copy plain text to the clipboard. Returns false if the browser blocked it. */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!import.meta.client || !text) return false
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** Trigger a browser download for a text file (YAML, etc.). */
export function downloadTextFile(
  filename: string,
  content: string,
  mimeType = 'text/yaml;charset=utf-8',
): void {
  if (!import.meta.client || !content) return
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
