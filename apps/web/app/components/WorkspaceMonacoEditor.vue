<script setup lang="ts">
import type { editor as MonacoEditor } from 'monaco-editor'

const props = defineProps<{
  modelValue: string
  path: string | null
  readOnly?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  save: []
}>()

const host = ref<HTMLElement | null>(null)
const monacoEditor = shallowRef<MonacoEditor.IStandaloneCodeEditor | null>(null)
const booting = ref(false)
const bootError = ref<string | null>(null)
let suppressModelUpdate = false
let bootToken = 0

function languageFromPath(path: string | null): string {
  if (!path) return 'plaintext'
  const lower = path.toLowerCase()
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'yaml'
  if (lower.endsWith('.json')) return 'json'
  if (lower.endsWith('.tf') || lower.endsWith('.tfvars') || lower.endsWith('.hcl')) return 'plaintext'
  if (lower.endsWith('.ts')) return 'typescript'
  if (lower.endsWith('.js')) return 'javascript'
  if (lower.endsWith('.md')) return 'markdown'
  if (lower.endsWith('.sh')) return 'shell'
  if (lower.includes('dockerfile') || lower.endsWith('containerfile')) return 'dockerfile'
  return 'plaintext'
}

async function loadLanguageSupport(language: string): Promise<void> {
  if (language === 'yaml') {
    await import('monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution')
    return
  }
  if (language === 'json') {
    await import('monaco-editor/esm/vs/language/json/monaco.contribution')
    return
  }
  if (language === 'typescript') {
    await import('monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution')
    return
  }
  if (language === 'javascript') {
    await import('monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution')
    return
  }
  if (language === 'markdown') {
    await import('monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution')
    return
  }
  if (language === 'shell') {
    await import('monaco-editor/esm/vs/basic-languages/shell/shell.contribution')
  }
}

async function installWorkers(language: string): Promise<void> {
  const EditorWorker = (
    await import('monaco-editor/esm/vs/editor/editor.worker?worker')
  ).default

  let JsonWorkerCtor: (new () => Worker) | null = null
  let TsWorkerCtor: (new () => Worker) | null = null

  if (language === 'json') {
    JsonWorkerCtor = (await import('monaco-editor/esm/vs/language/json/json.worker?worker')).default
  }
  if (language === 'typescript' || language === 'javascript') {
    TsWorkerCtor = (await import('monaco-editor/esm/vs/language/typescript/ts.worker?worker')).default
  }

  self.MonacoEnvironment = {
    getWorker(_: string, label: string) {
      if (label === 'json' && JsonWorkerCtor) return new JsonWorkerCtor()
      if ((label === 'typescript' || label === 'javascript') && TsWorkerCtor) {
        return new TsWorkerCtor()
      }
      return new EditorWorker()
    },
  }
}

async function boot() {
  if (!host.value || monacoEditor.value || booting.value) return

  const token = ++bootToken
  booting.value = true
  bootError.value = null

  try {
    const language = languageFromPath(props.path)
    const monaco = await import('monaco-editor')
    if (token !== bootToken) return

    await loadLanguageSupport(language)
    if (token !== bootToken) return

    await installWorkers(language)
    if (token !== bootToken || !host.value) return

    monaco.editor.defineTheme('launchpad-vscode', {
      base: 'vs-dark',
      inherit: true,
      rules: [],
      colors: {
        'editor.background': '#121a24',
        'editor.foreground': '#e8eef5',
        'editorLineNumber.foreground': '#8fa3b8',
        'editorLineNumber.activeForeground': '#e8eef5',
        'editor.selectionBackground': '#2dd4bf44',
        'editor.inactiveSelectionBackground': '#273447',
        'editorCursor.foreground': '#2dd4bf',
        'editor.lineHighlightBackground': '#182232',
        'editorIndentGuide.background1': '#273447',
        'editorIndentGuide.activeBackground1': '#8fa3b8',
        'editorGutter.background': '#121a24',
        'scrollbarSlider.background': '#8fa3b866',
        'scrollbarSlider.hoverBackground': '#8fa3b8aa',
        'scrollbarSlider.activeBackground': '#2dd4bf66',
      },
    })

    const instance = monaco.editor.create(host.value, {
      value: props.modelValue,
      language,
      theme: 'launchpad-vscode',
      automaticLayout: true,
      fontFamily: "'IBM Plex Mono', Menlo, Monaco, 'Courier New', monospace",
      fontSize: 13,
      lineHeight: 20,
      letterSpacing: 0,
      minimap: { enabled: false },
      lineNumbers: 'on',
      glyphMargin: false,
      folding: true,
      renderLineHighlight: 'line',
      scrollBeyondLastLine: false,
      smoothScrolling: true,
      cursorBlinking: 'smooth',
      tabSize: 2,
      insertSpaces: true,
      wordWrap: 'off',
      readOnly: props.readOnly ?? false,
      padding: { top: 8 },
      scrollbar: {
        verticalScrollbarSize: 10,
        horizontalScrollbarSize: 10,
      },
    })

    instance.onDidChangeModelContent(() => {
      if (suppressModelUpdate) return
      emit('update:modelValue', instance.getValue())
    })

    instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      emit('save')
    })

    monacoEditor.value = instance
  } catch (err) {
    if (token !== bootToken) return
    bootError.value = err instanceof Error ? err.message : 'Failed to load editor'
  } finally {
    if (token === bootToken) {
      booting.value = false
    }
  }
}

watch(
  () => props.modelValue,
  (value) => {
    const ed = monacoEditor.value
    if (!ed || ed.getValue() === value) return
    suppressModelUpdate = true
    ed.setValue(value)
    suppressModelUpdate = false
  },
)

watch(
  () => props.path,
  async (path) => {
    const ed = monacoEditor.value
    if (!ed) return
    const monaco = await import('monaco-editor')
    const model = ed.getModel()
    if (model) {
      monaco.editor.setModelLanguage(model, languageFromPath(path))
    }
  },
)

watch(
  () => props.readOnly,
  (readOnly) => {
    monacoEditor.value?.updateOptions({ readOnly: Boolean(readOnly) })
  },
)

onMounted(() => {
  // Yield one frame so the shell paints before Monaco downloads workers.
  requestAnimationFrame(() => {
    void boot()
  })
})

onUnmounted(() => {
  bootToken += 1
  monacoEditor.value?.dispose()
  monacoEditor.value = null
})
</script>

<template>
  <div class="relative h-full w-full">
    <div
      v-if="booting"
      class="absolute inset-0 z-10 flex items-center justify-center bg-[var(--lp-panel)]/80 text-xs text-[var(--lp-muted)]"
    >
      Loading editor…
    </div>
    <p
      v-else-if="bootError"
      class="absolute inset-0 z-10 flex items-center justify-center px-4 text-center text-xs text-[var(--lp-danger)]"
    >
      {{ bootError }}
    </p>
    <div ref="host" class="absolute inset-0 h-full w-full" />
  </div>
</template>
