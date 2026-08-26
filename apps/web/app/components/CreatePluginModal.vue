<script setup lang="ts">
import type { PluginFieldError, PluginManifestForm } from '~/types/pluginManifest'
import {
  PLUGIN_CATEGORIES,
  PLUGIN_CONFIG_TOOLS,
  PLUGIN_IAC_ENGINES,
  PLUGIN_PARENT_CLOUDS,
  PLUGIN_RUNNER_TYPES,
  PLUGIN_SERVICE_TYPES,
} from '~/types/pluginManifest'
import { ApiError } from '~/composables/useApi'
import {
  PLUGIN_BOILERPLATES,
  cloneForm,
  boilerplateById,
} from '~/utils/pluginManifestBoilerplates'
import {
  compilePluginManifest,
  errorForField,
  hydratePluginForm,
  isIacRunner,
  schemaEditorErrors,
  slugifyPluginId,
  validateCompiledManifest,
  pluginSchemaGeneratePayload,
} from '~/utils/pluginManifestForm'
import { dumpStructured, parseStructured, type YamlParseError } from '~/utils/yamlJson'
import { filterPluginCloudIcons, pluginIconLabel } from '~/utils/pluginCloudIcons'

const props = defineProps<{
  open: boolean
  /** Stored manifest JSON when editing an existing plugin. */
  initialManifest?: Record<string, unknown> | null
}>()

const emit = defineEmits<{
  close: []
  registered: [pluginId: string]
}>()

const { t } = useI18n()
const toast = useToast()
const { register, validate, generate, generateSchemas, saving } = useUserPlugins()

type ModalTab = 'identity' | 'runner' | 'schemas' | 'preview'

const tab = ref<ModalTab>('identity')
const boilerplateId = ref(PLUGIN_BOILERPLATES[0]?.id ?? 'digitalocean-droplets')
const form = reactive<PluginManifestForm>(cloneForm(PLUGIN_BOILERPLATES[0]?.form ?? hydratePluginForm({})))
const idTouched = ref(false)
const credentialsText = ref('')
const deploymentText = ref('')
const credentialsParseError = ref<YamlParseError | null>(null)
const deploymentParseError = ref<YamlParseError | null>(null)
const fieldErrors = ref<PluginFieldError[]>([])
const bannerError = ref<string | null>(null)
const validateNotice = ref<string | null>(null)
const validating = ref(false)
const schemaTab = ref<'credentials' | 'deployment'>('credentials')
const schemasReady = ref(false)
const iconOpen = ref(false)
const iconQuery = ref('')
const moreFieldsOpen = ref(false)
const aiPrompt = ref('')
const generating = ref(false)
const generatingSchemas = ref(false)
const schemaAiHint = ref('')
const aiSource = ref<string | null>(null)
const schemaAiSource = ref<string | null>(null)
const filteredIcons = computed(() => filterPluginCloudIcons(iconQuery.value))
const selectedIconLabel = computed(() => pluginIconLabel(form.icon || 'cloud'))

const keywordsText = computed({
  get: () => form.keywords.join(', '),
  set: (value: string) => {
    form.keywords = value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
  },
})

let applyingDump = false

const editing = computed(() => Boolean(props.initialManifest && str(props.initialManifest.id)))
const compiled = computed(() => compilePluginManifest(form))
const previewText = computed(() => dumpStructured(compiled.value, 'yaml'))
const localErrors = computed(() => validateCompiledManifest(compiled.value))
const allErrors = computed(() => [...localErrors.value, ...fieldErrors.value])
const identityError = computed(() => ({
  id: errorForField(allErrors.value, 'id'),
  label: errorForField(allErrors.value, 'label'),
  version: errorForField(allErrors.value, 'version'),
  category: errorForField(allErrors.value, 'category'),
  description: errorForField(allErrors.value, 'description'),
}))
const schemaErrors = computed(() => schemaEditorErrors(allErrors.value))
const credentialsEditorPath = computed(() => 'credentials.schema.yaml')
const deploymentEditorPath = computed(() => 'deployment.schema.yaml')
const runnerTargetLabel = computed(() =>
  form.runnerType === 'ansible'
    ? 'Playbook path'
    : isIacRunner(form.runnerType)
      ? t('cloudPlugins.registerModal.bundlePath')
      : t('cloudPlugins.registerModal.entrypoint')
)
const provisionTargetLabel = computed(() =>
  form.provisionRunnerType === 'ansible'
    ? 'Playbook path'
    : isIacRunner(form.provisionRunnerType)
      ? t('cloudPlugins.registerModal.bundlePath')
      : t('cloudPlugins.registerModal.entrypoint')
)
const configTargetLabel = computed(() =>
  form.configRunnerType === 'ansible'
    ? 'Playbook path'
    : isIacRunner(form.configRunnerType)
      ? t('cloudPlugins.registerModal.bundlePath')
      : t('cloudPlugins.registerModal.entrypoint')
)
const title = computed(() =>
  editing.value ? t('cloudPlugins.registerModal.editTitle') : t('cloudPlugins.registerModal.title'),
)

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function dumpEditors() {
  applyingDump = true
  credentialsText.value = dumpStructured(form.credentialsSchema, 'yaml')
  deploymentText.value = dumpStructured(form.deploymentConfigSchema, 'yaml')
  credentialsParseError.value = null
  deploymentParseError.value = null
  nextTick(() => {
    applyingDump = false
  })
}

function applyForm(next: PluginManifestForm) {
  Object.assign(form, cloneForm(next))
  idTouched.value = editing.value
  dumpEditors()
}

function resetFromOpen() {
  tab.value = 'identity'
  schemaTab.value = 'credentials'
  moreFieldsOpen.value = false
  aiPrompt.value = ''
  aiSource.value = null
  schemaAiHint.value = ''
  schemaAiSource.value = null
  fieldErrors.value = []
  bannerError.value = null
  validateNotice.value = null
  if (props.initialManifest) {
    applyForm(hydratePluginForm(props.initialManifest))
    boilerplateId.value = ''
    return
  }
  const preset = PLUGIN_BOILERPLATES[0]
  boilerplateId.value = preset?.id ?? ''
  applyForm(preset ? cloneForm(preset.form) : hydratePluginForm({}))
}

function onBoilerplateChange(id: string) {
  const preset = boilerplateById(id)
  if (!preset) return
  applyForm(cloneForm(preset.form))
  fieldErrors.value = []
  bannerError.value = null
}

function onLabelBlur() {
  if (!idTouched.value && form.label.trim()) {
    form.id = slugifyPluginId(form.label)
  }
}

function onIdInput(event: Event) {
  idTouched.value = true
  const target = event.target as HTMLInputElement
  form.id = slugifyPluginId(target.value)
}

async function onGenerate() {
  bannerError.value = null
  aiSource.value = null
  const prompt = aiPrompt.value.trim()
  if (prompt.length < 8) {
    bannerError.value = t('cloudPlugins.registerModal.aiPromptShort')
    return
  }
  generating.value = true
  try {
    const result = await generate(prompt)
    applyForm(hydratePluginForm(result.manifest))
    moreFieldsOpen.value = Boolean(form.parentCloud || form.docsUrl || form.homepage)
    aiSource.value = result.source
    const notice =
      result.source === 'gemini'
        ? t('cloudPlugins.registerModal.aiFromGemini')
        : t('cloudPlugins.registerModal.aiFromHeuristic')
    toast.success(t('cloudPlugins.registerModal.aiDone'), notice)
  } catch (err) {
    bannerError.value = err instanceof Error ? err.message : t('cloudPlugins.registerModal.aiFailed')
    toast.error(t('cloudPlugins.registerModal.aiFailed'), bannerError.value)
  } finally {
    generating.value = false
  }
}

function asSchemaRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
}

async function onGenerateSchemas() {
  bannerError.value = null
  schemaAiSource.value = null
  generatingSchemas.value = true
  try {
    const result = await generateSchemas(pluginSchemaGeneratePayload(form, schemaAiHint.value))
    form.credentialsSchema = asSchemaRecord(result.credentialsSchema)
    form.deploymentConfigSchema = asSchemaRecord(result.deploymentConfigSchema)
    dumpEditors()
    schemaAiSource.value = result.source
    const notice =
      result.source === 'gemini'
        ? t('cloudPlugins.registerModal.aiFromGemini')
        : t('cloudPlugins.registerModal.aiFromHeuristic')
    toast.success(t('cloudPlugins.registerModal.aiSchemasDone'), notice)
  } catch (err) {
    bannerError.value = err instanceof Error ? err.message : t('cloudPlugins.registerModal.aiSchemasFailed')
    toast.error(t('cloudPlugins.registerModal.aiSchemasFailed'), bannerError.value)
  } finally {
    generatingSchemas.value = false
  }
}

function applyParsed(
  text: string,
  target: 'credentials' | 'deployment',
) {
  if (applyingDump) return
  const parsed = parseStructured(text, 'yaml')
  if (target === 'credentials') {
    credentialsParseError.value = parsed.error
    if (parsed.value) form.credentialsSchema = parsed.value
    return
  }
  deploymentParseError.value = parsed.error
  if (parsed.value) form.deploymentConfigSchema = parsed.value
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      schemasReady.value = false
      iconOpen.value = false
      iconQuery.value = ''
      resetFromOpen()
      if (import.meta.client) window.addEventListener('keydown', onKeydown)
      return
    }
    if (import.meta.client) window.removeEventListener('keydown', onKeydown)
  },
  { immediate: true },
)

watch(
  () => props.initialManifest,
  (manifest) => {
    if (props.open && manifest) {
      applyForm(hydratePluginForm(manifest))
      boilerplateId.value = ''
    }
  },
)

watch(tab, (next) => {
  if (next === 'schemas') schemasReady.value = true
})


watch(credentialsText, (text) => applyParsed(text, 'credentials'))
watch(deploymentText, (text) => applyParsed(text, 'deployment'))

function close() {
  emit('close')
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

onUnmounted(() => {
  if (import.meta.client) window.removeEventListener('keydown', onKeydown)
})

function collectEditorErrors(): PluginFieldError[] {
  const extra: PluginFieldError[] = []
  if (credentialsParseError.value) {
    extra.push({
      loc: 'credentialsSchema',
      msg: `line ${credentialsParseError.value.line}: ${credentialsParseError.value.message}`,
    })
  }
  if (deploymentParseError.value) {
    extra.push({
      loc: 'deploymentConfigSchema',
      msg: `line ${deploymentParseError.value.line}: ${deploymentParseError.value.message}`,
    })
  }
  return extra
}

function applyServerErrors(errors: PluginFieldError[]) {
  fieldErrors.value = errors
  if (errors.some((err) => err.loc.startsWith('runner') || err.loc.startsWith('capabilities'))) {
    tab.value = 'runner'
    return
  }
  if (
    errors.some(
      (err) =>
        err.loc.includes('credentials') ||
        err.loc.includes('deployment'),
    )
  ) {
    tab.value = 'schemas'
    return
  }
  if (errors.some((err) => ['id', 'label', 'version', 'category', 'description', 'icon'].includes(err.loc))) {
    tab.value = 'identity'
  }
}

async function onValidate() {
  bannerError.value = null
  validateNotice.value = null
  const editorErrors = collectEditorErrors()
  if (editorErrors.length > 0) {
    fieldErrors.value = editorErrors
    tab.value = 'schemas'
    return
  }
  const local = validateCompiledManifest(compiled.value)
  if (local.length > 0) {
    applyServerErrors(local)
    bannerError.value = local.map((err) => `${err.loc}: ${err.msg}`).join('; ')
    return
  }
  validating.value = true
  try {
    const result = await validate(compiled.value as unknown as Record<string, unknown>)
    fieldErrors.value = result.errors
    if (!result.valid) {
      applyServerErrors(result.errors)
      bannerError.value = result.errors.map((err) => `${err.loc}: ${err.msg}`).join('; ')
      toast.error(t('cloudPlugins.registerModal.validateFailed'), bannerError.value)
      return
    }
    validateNotice.value = t('cloudPlugins.registerModal.validateOk')
    toast.success(t('cloudPlugins.registerModal.validateOk'))
  } catch (err) {
    bannerError.value = err instanceof Error ? err.message : t('cloudPlugins.registerModal.validateFailed')
    toast.error(t('cloudPlugins.registerModal.validateFailed'), bannerError.value)
  } finally {
    validating.value = false
  }
}

async function onSave() {
  bannerError.value = null
  validateNotice.value = null
  const editorErrors = collectEditorErrors()
  if (editorErrors.length > 0) {
    applyServerErrors(editorErrors)
    tab.value = 'schemas'
    return
  }
  const local = validateCompiledManifest(compiled.value)
  if (local.length > 0) {
    applyServerErrors(local)
    bannerError.value = local.map((err) => `${err.loc}: ${err.msg}`).join('; ')
    return
  }
  try {
    await register(compiled.value as unknown as Record<string, unknown>, {
      owner: form.owner,
      visibility: form.visibility,
    })
    toast.success(t('cloudPlugins.pluginRegistered'), form.label)
    emit('registered', compiled.value.id)
    close()
  } catch (err) {
    const details = err instanceof ApiError ? err.details : null
    const errors = Array.isArray(details?.errors) ? (details.errors as PluginFieldError[]) : []
    if (errors.length > 0) {
      applyServerErrors(errors)
      bannerError.value = errors.map((item) => `${item.loc}: ${item.msg}`).join('; ')
    } else {
      bannerError.value = err instanceof Error ? err.message : t('cloudPlugins.registerModal.saveFailed')
    }
    toast.error(t('cloudPlugins.registerModal.saveFailed'), bannerError.value)
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4 backdrop-blur-md"
      @click.self="close"
    >
      <div class="flex h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl">
        <header class="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--lp-line)] px-5 py-4">
          <div class="min-w-0">
            <h2 class="text-base font-semibold text-[var(--lp-text)]">{{ title }}</h2>
            <p class="mt-1 text-[12px] text-[var(--lp-muted)]">
              {{ t('cloudPlugins.registerModal.subtitle') }}
            </p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <label class="flex items-center gap-2 text-[11px] text-[var(--lp-muted)]">
              <span>{{ t('cloudPlugins.registerModal.boilerplate') }}</span>
              <select
                v-model="boilerplateId"
                class="lp-input lp-input-inline text-xs"
                :disabled="editing"
                @change="onBoilerplateChange(boilerplateId)"
              >
                <option v-if="editing" value="">{{ t('cloudPlugins.registerModal.existing') }}</option>
                <option v-for="item in PLUGIN_BOILERPLATES" :key="item.id" :value="item.id">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <button
              type="button"
              class="rounded-lg p-1.5 text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2,rgba(0,0,0,0.06))] hover:text-[var(--lp-text)]"
              @click="close"
            >
              <span class="material-symbols-outlined text-xl">close</span>
            </button>
          </div>
        </header>

        <nav class="flex shrink-0 gap-1 border-b border-[var(--lp-line)] px-4 pt-2">
          <button
            v-for="item in [
              { id: 'identity', label: t('cloudPlugins.registerModal.tabIdentity') },
              { id: 'runner', label: t('cloudPlugins.registerModal.tabRunner') },
              { id: 'schemas', label: t('cloudPlugins.registerModal.tabSchemas') },
              { id: 'preview', label: t('cloudPlugins.registerModal.tabPreview') },
            ]"
            :key="item.id"
            type="button"
            class="rounded-t-md px-3 py-2 text-xs font-medium"
            :class="tab === item.id
              ? 'bg-[var(--lp-accent-soft,rgba(99,102,241,0.12))] text-[var(--lp-text)]'
              : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
            @click="tab = item.id as ModalTab"
          >
            {{ item.label }}
          </button>
        </nav>

        <div
          v-if="bannerError"
          class="mx-5 mt-3 rounded-md border border-[var(--lp-danger,#e5484d)]/40 bg-[var(--lp-danger,#e5484d)]/10 px-3 py-2 text-[11px] text-[var(--lp-danger,#e5484d)]"
        >
          {{ bannerError }}
        </div>
        <div
          v-else-if="validateNotice"
          class="mx-5 mt-3 rounded-md border border-[var(--lp-success,#22c55e)]/40 bg-[var(--lp-success,#22c55e)]/10 px-3 py-2 text-[11px] text-[var(--lp-success,#22c55e)]"
        >
          {{ validateNotice }}
        </div>

        <div class="min-h-0 flex-1 overflow-hidden">
          <div v-show="tab === 'identity'" class="grid h-full auto-rows-min content-start items-start gap-x-3 gap-y-1.5 overflow-auto p-4 sm:grid-cols-2">
            <div class="flex flex-col gap-1.5 rounded-md border border-[var(--lp-line)] p-2 sm:col-span-2">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.aiPrompt') }}</label>
              <textarea
                v-model="aiPrompt"
                rows="2"
                class="lp-input py-1.5 text-xs leading-5"
                :placeholder="t('cloudPlugins.registerModal.aiPromptPlaceholder')"
              />
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.registerModal.aiHint') }}</p>
                <button
                  type="button"
                  class="lp-btn-ghost inline-flex items-center gap-1 text-xs"
                  :disabled="generating"
                  @click="onGenerate"
                >
                  <span class="material-symbols-outlined text-sm text-amber-400">auto_awesome</span>
                  {{ generating ? t('cloudPlugins.registerModal.aiGenerating') : t('cloudPlugins.registerModal.aiGenerate') }}
                </button>
              </div>
              <p v-if="aiSource" class="text-[11px] text-[var(--lp-muted)]">
                {{ aiSource === 'gemini' ? t('cloudPlugins.registerModal.aiFromGemini') : t('cloudPlugins.registerModal.aiFromHeuristic') }}
              </p>
            </div>
            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.id') }}</label>
              <input
                :value="form.id"
                class="lp-input py-1.5 text-xs leading-5"
                :disabled="editing"
                :placeholder="t('cloudPlugins.registerModal.idPlaceholder')"
                @input="onIdInput"
              >
              <p v-if="identityError.id" class="text-[11px] text-[var(--lp-danger,#e5484d)]">{{ identityError.id }}</p>
            </div>
            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.label') }}</label>
              <input
                v-model="form.label"
                class="lp-input py-1.5 text-xs leading-5"
                :placeholder="t('cloudPlugins.registerModal.labelPlaceholder')"
                @blur="onLabelBlur"
              >
              <p v-if="identityError.label" class="text-[11px] text-[var(--lp-danger,#e5484d)]">{{ identityError.label }}</p>
            </div>
            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.version') }}</label>
              <input v-model="form.version" class="lp-input py-1.5 text-xs leading-5" placeholder="1.0.0">
              <p v-if="identityError.version" class="text-[11px] text-[var(--lp-danger,#e5484d)]">{{ identityError.version }}</p>
            </div>
            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.category') }}</label>
              <select v-model="form.category" class="lp-input py-1.5 text-xs leading-5">
                <option v-for="item in PLUGIN_CATEGORIES" :key="item" :value="item">{{ item }}</option>
              </select>
            </div>
            <div class="flex flex-col gap-0.5 sm:col-span-2">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.description') }}</label>
              <textarea v-model="form.description" rows="2" class="lp-input py-1.5 text-xs leading-5" />
              <p v-if="identityError.description" class="text-[11px] text-[var(--lp-danger,#e5484d)]">{{ identityError.description }}</p>
            </div>
            <div class="relative flex flex-col gap-0.5 sm:col-span-2">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.icon') }}</label>
              <button
                type="button"
                class="lp-input flex items-center gap-2 py-1.5 text-left text-xs leading-5"
                @click="iconOpen = !iconOpen"
              >
                <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">{{ form.icon || 'cloud' }}</span>
                <span class="flex-1 truncate">{{ selectedIconLabel }}</span>
                <span class="material-symbols-outlined text-sm text-[var(--lp-muted)]">expand_more</span>
              </button>
              <div
                v-if="iconOpen"
                class="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-hidden rounded-md border border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-lg"
              >
                <input
                  v-model="iconQuery"
                  class="lp-input rounded-none border-0 border-b border-[var(--lp-line)] py-1.5 text-xs"
                  :placeholder="t('cloudPlugins.registerModal.iconSearch')"
                >
                <div class="grid max-h-52 grid-cols-5 gap-0.5 overflow-auto p-1.5 sm:grid-cols-7">
                  <button
                    v-for="item in filteredIcons"
                    :key="item.glyph"
                    type="button"
                    class="flex flex-col items-center gap-0.5 rounded px-0.5 py-1 text-[9px] text-[var(--lp-muted)] hover:bg-[var(--lp-accent-soft,rgba(99,102,241,0.12))] hover:text-[var(--lp-text)]"
                    :class="form.icon === item.glyph ? 'bg-[var(--lp-accent-soft,rgba(99,102,241,0.12))] text-[var(--lp-text)]' : ''"
                    :title="item.label"
                    @click="form.icon = item.glyph; iconOpen = false; iconQuery = ''"
                  >
                    <span class="material-symbols-outlined text-lg">{{ item.glyph }}</span>
                    <span class="w-full truncate text-center">{{ item.label }}</span>
                  </button>
                </div>
              </div>
            </div>
            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.owner') }}</label>
              <div class="flex gap-3 text-xs text-[var(--lp-text)]">
                <label class="flex items-center gap-1.5">
                  <input v-model="form.owner" type="radio" value="user">
                  {{ t('cloudPlugins.registerModal.ownerUser') }}
                </label>
                <label class="flex items-center gap-1.5">
                  <input v-model="form.owner" type="radio" value="organization">
                  {{ t('cloudPlugins.registerModal.ownerOrg') }}
                </label>
              </div>
            </div>
            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.visibility') }}</label>
              <label class="flex items-center gap-1.5 text-xs text-[var(--lp-text)]">
                <input v-model="form.visibility" type="checkbox" true-value="public" false-value="private">
                {{ t('cloudPlugins.registerModal.publish') }}
              </label>
            </div>
            <p class="text-[11px] text-[var(--lp-muted)] sm:col-span-2">
              {{ t('cloudPlugins.registerModal.ownerHint') }}
            </p>
            <div class="sm:col-span-2">
              <button
                type="button"
                class="text-[11px] text-[var(--lp-accent)] hover:underline"
                @click="moreFieldsOpen = !moreFieldsOpen"
              >
                {{ moreFieldsOpen ? t('cloudPlugins.registerModal.hideMore') : t('cloudPlugins.registerModal.showMore') }}
              </button>
            </div>
            <template v-if="moreFieldsOpen">
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.parentCloud') }}</label>
                <select v-model="form.parentCloud" class="lp-input py-1.5 text-xs leading-5">
                  <option value="">{{ t('cloudPlugins.registerModal.parentCloudNone') }}</option>
                  <option v-for="item in PLUGIN_PARENT_CLOUDS" :key="item" :value="item">{{ item }}</option>
                </select>
                <p class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.registerModal.parentCloudHint') }}</p>
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.author') }}</label>
                <input v-model="form.author" class="lp-input py-1.5 text-xs leading-5">
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.license') }}</label>
                <input v-model="form.license" class="lp-input py-1.5 text-xs leading-5" placeholder="Apache-2.0">
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.keywords') }}</label>
                <input v-model="keywordsText" class="lp-input py-1.5 text-xs leading-5" :placeholder="t('cloudPlugins.registerModal.keywordsPlaceholder')">
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.docsUrl') }}</label>
                <input v-model="form.docsUrl" class="lp-input py-1.5 text-xs leading-5" placeholder="https://">
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.homepage') }}</label>
                <input v-model="form.homepage" class="lp-input py-1.5 text-xs leading-5" placeholder="https://">
              </div>
            </template>
          </div>

          <div v-show="tab === 'runner'" class="grid h-full auto-rows-min content-start items-start gap-x-3 gap-y-1.5 overflow-auto p-4 sm:grid-cols-2">
            <div class="flex flex-col gap-0.5 sm:col-span-2">
              <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
                <input v-model="form.useStackRunners" type="checkbox">
                Use separate provision + config runners
              </label>
            </div>

            <template v-if="!form.useStackRunners">
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ t('cloudPlugins.registerModal.runnerType') }}</label>
                <select v-model="form.runnerType" class="lp-input py-1.5 text-xs leading-5">
                  <option v-for="item in PLUGIN_RUNNER_TYPES" :key="item" :value="item">{{ item }}</option>
                </select>
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ runnerTargetLabel }}</label>
                <input v-model="form.runnerTarget" class="lp-input py-1.5 font-mono text-xs leading-5" placeholder="digitalocean">
              </div>
            </template>

            <template v-else>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">Provision runner type</label>
                <select v-model="form.provisionRunnerType" class="lp-input py-1.5 text-xs leading-5">
                  <option v-for="item in PLUGIN_RUNNER_TYPES" :key="item" :value="item">{{ item }}</option>
                </select>
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ provisionTargetLabel }}</label>
                <input v-model="form.provisionRunnerTarget" class="lp-input py-1.5 font-mono text-xs leading-5" placeholder="provision">
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">Config runner type</label>
                <select v-model="form.configRunnerType" class="lp-input py-1.5 text-xs leading-5">
                  <option v-for="item in PLUGIN_RUNNER_TYPES" :key="item" :value="item">{{ item }}</option>
                </select>
              </div>
              <div class="flex flex-col gap-0.5">
                <label class="lp-label">{{ configTargetLabel }}</label>
                <input v-model="form.configRunnerTarget" class="lp-input py-1.5 font-mono text-xs leading-5" placeholder="config/site.yml">
              </div>
            </template>

            <div class="flex flex-col gap-0.5">
              <label class="lp-label">{{ t('cloudPlugins.registerModal.serviceType') }}</label>
              <select v-model="form.serviceType" class="lp-input py-1.5 text-xs leading-5">
                <option v-for="item in PLUGIN_SERVICE_TYPES" :key="item" :value="item">{{ item }}</option>
              </select>
            </div>

            <div class="flex flex-col gap-1.5 sm:col-span-2">
              <p class="text-[11px] font-medium text-[var(--lp-muted)]">{{ t('cloudPlugins.registerModal.capabilities') }}</p>
              <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
                <input v-model="form.supportsTtl" type="checkbox">
                {{ t('cloudPlugins.registerModal.supportsTtl') }}
              </label>
              <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
                <input v-model="form.supportsCustomDns" type="checkbox">
                {{ t('cloudPlugins.registerModal.supportsCustomDns') }}
              </label>
              <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
                <input v-model="form.supportsEphemeralDb" type="checkbox">
                {{ t('cloudPlugins.registerModal.supportsEphemeralDb') }}
              </label>
            </div>

            <div class="flex flex-col gap-1.5 sm:col-span-2">
              <p class="text-[11px] font-medium text-[var(--lp-muted)]">Defaults when selected as deploy target</p>
              <div class="grid gap-3 sm:grid-cols-2">
                <div class="flex flex-col gap-0.5">
                  <label class="lp-label">Default provision engine</label>
                  <select v-model="form.defaultIacEngine" class="lp-input py-1.5 text-xs leading-5">
                    <option v-for="item in PLUGIN_IAC_ENGINES" :key="item" :value="item">{{ item }}</option>
                  </select>
                </div>
                <div class="flex flex-col gap-0.5">
                  <label class="lp-label">Default config tool</label>
                  <select v-model="form.defaultConfigTool" class="lp-input py-1.5 text-xs leading-5">
                    <option v-for="item in PLUGIN_CONFIG_TOOLS" :key="item" :value="item">{{ item }}</option>
                  </select>
                </div>
              </div>
            </div>
          </div>

          <div v-show="tab === 'schemas'" class="flex h-full min-h-0 flex-col gap-3 p-4">
            <div class="flex flex-col gap-1.5 rounded-md border border-[var(--lp-line)] p-2">
              <p class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.registerModal.aiSchemasHint') }}</p>
              <textarea
                v-model="schemaAiHint"
                rows="2"
                class="lp-input py-1.5 text-xs leading-5"
                :placeholder="t('cloudPlugins.registerModal.aiSchemasPlaceholder')"
              />
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-[11px] text-[var(--lp-muted)]">
                  {{
                    form.parentCloud
                      ? t('cloudPlugins.registerModal.aiSchemasService', {
                          cloud: form.parentCloud,
                          service: form.serviceType,
                        })
                      : t('cloudPlugins.registerModal.aiSchemasNoParent')
                  }}
                </p>
                <button
                  type="button"
                  class="lp-btn-ghost inline-flex items-center gap-1 text-xs"
                  :disabled="generatingSchemas"
                  @click="onGenerateSchemas"
                >
                  <span class="material-symbols-outlined text-sm text-amber-400">auto_awesome</span>
                  {{ generatingSchemas ? t('cloudPlugins.registerModal.aiGenerating') : t('cloudPlugins.registerModal.aiGenerateSchemas') }}
                </button>
              </div>
              <p v-if="schemaAiSource" class="text-[11px] text-[var(--lp-muted)]">
                {{ schemaAiSource === 'gemini' ? t('cloudPlugins.registerModal.aiFromGemini') : t('cloudPlugins.registerModal.aiFromHeuristic') }}
              </p>
            </div>
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="flex gap-1">
                <button
                  type="button"
                  class="rounded px-2 py-1 text-[11px]"
                  :class="schemaTab === 'credentials' ? 'bg-[var(--lp-accent-soft,rgba(99,102,241,0.12))] text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'"
                  @click="schemaTab = 'credentials'"
                >
                  credentialsSchema
                </button>
                <button
                  type="button"
                  class="rounded px-2 py-1 text-[11px]"
                  :class="schemaTab === 'deployment' ? 'bg-[var(--lp-accent-soft,rgba(99,102,241,0.12))] text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'"
                  @click="schemaTab = 'deployment'"
                >
                  deploymentConfigSchema
                </button>
              </div>
              <span class="px-2 py-1 text-[11px] text-[var(--lp-muted)]">YAML</span>
            </div>
            <p
              v-if="schemaTab === 'credentials' && (credentialsParseError || schemaErrors.credentials)"
              class="text-[11px] text-[var(--lp-danger,#e5484d)]"
            >
              <template v-if="credentialsParseError">
                {{ t('cloudPlugins.registerModal.lineError', { line: credentialsParseError.line, message: credentialsParseError.message }) }}
              </template>
              <template v-else>{{ schemaErrors.credentials }}</template>
            </p>
            <p
              v-else-if="schemaTab === 'deployment' && (deploymentParseError || schemaErrors.deployment)"
              class="text-[11px] text-[var(--lp-danger,#e5484d)]"
            >
              <template v-if="deploymentParseError">
                {{ t('cloudPlugins.registerModal.lineError', { line: deploymentParseError.line, message: deploymentParseError.message }) }}
              </template>
              <template v-else>{{ schemaErrors.deployment }}</template>
            </p>
            <div v-if="schemasReady" class="relative min-h-0 flex-1 overflow-hidden rounded-md border border-[var(--lp-line)]">
              <WorkspaceMonacoEditor
                v-show="schemaTab === 'credentials'"
                v-model="credentialsText"
                :path="credentialsEditorPath"
              />
              <WorkspaceMonacoEditor
                v-show="schemaTab === 'deployment'"
                v-model="deploymentText"
                :path="deploymentEditorPath"
              />
            </div>
          </div>

          <div v-show="tab === 'preview'" class="flex h-full min-h-0 flex-col gap-3 p-4">
            <p class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.registerModal.previewHint') }}</p>
            <pre class="min-h-0 flex-1 overflow-auto rounded-md border border-[var(--lp-line)] bg-[var(--lp-panel-2,rgba(0,0,0,0.04))] p-3 font-mono text-[11px] leading-relaxed text-[var(--lp-text)]">{{ previewText }}</pre>
          </div>
        </div>

        <footer class="flex shrink-0 items-center justify-between gap-2 border-t border-[var(--lp-line)] px-5 py-3">
          <button type="button" class="lp-btn-ghost text-xs" :disabled="validating" @click="onValidate">
            {{ validating ? t('cloudPlugins.registerModal.validating') : t('cloudPlugins.registerModal.validate') }}
          </button>
          <div class="flex items-center gap-2">
            <button type="button" class="lp-btn-ghost text-xs" @click="close">
              {{ t('common.cancel') }}
            </button>
            <button type="button" class="lp-btn-primary text-xs" :disabled="saving" @click="onSave">
              {{ saving ? t('cloudPlugins.saving') : t('cloudPlugins.registerModal.save') }}
            </button>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>
