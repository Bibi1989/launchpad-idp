<script setup lang="ts">
/**
 * Branch select for GitHub/GitLab repos. Loads remote branches and optionally
 * lets the user name a new branch (created on push for GitHub).
 */
import type { GitHost } from '~/types/git'
import type { GitBranchItem, GitBranchListResponse } from '~/types/provisioning'

const CREATE_VALUE = '__lp_create_branch__'

const props = withDefaults(
  defineProps<{
    modelValue: string
    host: GitHost
    installationId?: number | null
    fullName?: string | null
    projectPath?: string | null
    /** When true, offer "Create new branch" (used for GitHub push flows). */
    allowCreate?: boolean
    disabled?: boolean
    label?: string
  }>(),
  {
    installationId: null,
    fullName: null,
    projectPath: null,
    allowCreate: false,
    disabled: false,
    label: undefined,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { t } = useI18n()
const { listGithubBranches, listGitlabBranches } = useProvisioning()

const loading = ref(false)
const loadError = ref<string | null>(null)
const branches = ref<GitBranchItem[]>([])
const creating = ref(false)
const draftName = ref('')

const canFetch = computed(() => {
  if (props.host === 'github') {
    return Boolean(props.installationId && props.fullName?.includes('/'))
  }
  return Boolean(props.projectPath?.trim())
})

const selectValue = computed(() => {
  if (creating.value) return CREATE_VALUE
  return props.modelValue || ''
})

async function loadBranches() {
  if (!canFetch.value) {
    branches.value = []
    loadError.value = null
    return
  }
  loading.value = true
  loadError.value = null
  try {
    let res: GitBranchListResponse
    if (props.host === 'github') {
      res = await listGithubBranches({
        installationId: props.installationId!,
        fullName: props.fullName!.trim(),
      })
    } else {
      res = await listGitlabBranches({
        pathWithNamespace: props.projectPath!.trim(),
      })
    }
    branches.value = res.branches
    const current = props.modelValue.trim()
    const names = new Set(res.branches.map((b) => b.name))
    if (current && !names.has(current) && props.allowCreate) {
      creating.value = true
      draftName.value = current
      return
    }
    creating.value = false
    if (!current || !names.has(current)) {
      const fallback =
        res.default_branch
        || res.branches.find((b) => b.is_default)?.name
        || res.branches[0]?.name
        || 'main'
      emit('update:modelValue', fallback)
    }
  } catch (err) {
    branches.value = []
    loadError.value = err instanceof Error ? err.message : t('gitBranch.loadFailed')
  } finally {
    loading.value = false
  }
}

function onSelectChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value === CREATE_VALUE) {
    creating.value = true
    draftName.value = props.modelValue.trim() || ''
    return
  }
  creating.value = false
  draftName.value = ''
  emit('update:modelValue', value)
}

function onDraftInput() {
  const name = draftName.value.trim()
  emit('update:modelValue', name || 'main')
}

function cancelCreate() {
  creating.value = false
  draftName.value = ''
  const fallback =
    branches.value.find((b) => b.is_default)?.name
    || branches.value[0]?.name
    || 'main'
  emit('update:modelValue', fallback)
}

watch(
  () => [props.host, props.installationId, props.fullName, props.projectPath] as const,
  () => {
    void loadBranches()
  },
  { immediate: true },
)
</script>

<template>
  <div class="block space-y-1.5">
    <span class="lp-label">{{ label || t('common.branch') }}</span>
    <template v-if="!canFetch">
      <input
        :value="modelValue"
        type="text"
        class="lp-input w-full font-mono text-sm"
        autocomplete="off"
        placeholder="main"
        :disabled="disabled"
        @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
      >
      <p class="text-xs text-[var(--lp-muted)]">
        {{ t('gitBranch.selectRepoFirst') }}
      </p>
    </template>
    <template v-else>
      <select
        class="lp-input w-full font-mono text-sm"
        :value="selectValue"
        :disabled="disabled || loading"
        @change="onSelectChange"
      >
        <option v-if="loading" value="" disabled>
          {{ t('common.loading') }}
        </option>
        <option
          v-for="branch in branches"
          :key="branch.name"
          :value="branch.name"
        >
          {{ branch.name }}{{ branch.is_default ? ` (${t('gitBranch.default')})` : '' }}
        </option>
        <option
          v-if="allowCreate"
          :value="CREATE_VALUE"
        >
          {{ t('gitBranch.createNew') }}
        </option>
        <option
          v-if="!loading && !branches.length && !allowCreate"
          value="main"
        >
          main
        </option>
      </select>
      <div v-if="creating && allowCreate" class="flex items-center gap-2">
        <input
          v-model="draftName"
          type="text"
          class="lp-input flex-1 font-mono text-sm"
          autocomplete="off"
          :placeholder="t('gitBranch.newPlaceholder')"
          :disabled="disabled"
          @input="onDraftInput"
        >
        <button
          type="button"
          class="lp-btn-ghost text-xs"
          :disabled="disabled"
          @click="cancelCreate"
        >
          {{ t('common.cancel') }}
        </button>
      </div>
      <p v-if="loadError" class="text-xs text-[var(--lp-danger)]">{{ loadError }}</p>
      <p v-else-if="allowCreate && creating" class="text-xs text-[var(--lp-muted)]">
        {{ t('gitBranch.createHint') }}
      </p>
    </template>
  </div>
</template>
