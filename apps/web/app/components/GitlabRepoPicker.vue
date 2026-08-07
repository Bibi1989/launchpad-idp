<script setup lang="ts">
import type { GitlabProjectItem } from '~/types/provisioning'

const props = withDefaults(
  defineProps<{
    modelValue: string
    disabled?: boolean
  }>(),
  {
    disabled: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  selectProject: [project: GitlabProjectItem]
}>()

const { listGitlabProjects } = useProvisioning()
const { t } = useI18n()

const searchQuery = ref('')
const isOpen = ref(false)
const loading = ref(false)
const projects = ref<GitlabProjectItem[]>([])
const searchError = ref<string | null>(null)

const triggerButton = ref<HTMLElement | null>(null)
const dropdownContainer = ref<HTMLElement | null>(null)
const dropdownStyle = ref<Record<string, string>>({})

const filteredProjects = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return projects.value
  return projects.value.filter(
    (p) =>
      p.name.toLowerCase().includes(q) ||
      p.path_with_namespace.toLowerCase().includes(q),
  )
})

const selectedProject = computed(
  () => projects.value.find((p) => p.path_with_namespace === props.modelValue) || null,
)

function isPrivate(visibility: string): boolean {
  return (visibility || '').toLowerCase() !== 'public'
}

function updatePosition() {
  if (!triggerButton.value) return
  const rect = triggerButton.value.getBoundingClientRect()
  dropdownStyle.value = {
    position: 'fixed',
    top: `${rect.bottom + 4}px`,
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    zIndex: '999999',
  }
}

async function fetchProjects(query = '') {
  loading.value = true
  searchError.value = null
  try {
    projects.value = await listGitlabProjects({
      q: query.trim() || undefined,
    })
  } catch (err) {
    searchError.value = err instanceof Error ? err.message : t('integrations.gitlabProjectsLoadFailed')
  } finally {
    loading.value = false
  }
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null

function onSearchInput(val: string) {
  searchQuery.value = val
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    void fetchProjects(searchQuery.value)
  }, 300)
}

function selectProject(project: GitlabProjectItem) {
  emit('update:modelValue', project.path_with_namespace)
  emit('selectProject', project)
  isOpen.value = false
}

function toggleOpen() {
  if (props.disabled) return
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    nextTick(() => updatePosition())
    if (projects.value.length === 0) {
      void fetchProjects(searchQuery.value)
    }
  }
}

function onClickOutside(event: MouseEvent) {
  const target = event.target as Node
  if (
    dropdownContainer.value &&
    !dropdownContainer.value.contains(target) &&
    triggerButton.value &&
    !triggerButton.value.contains(target)
  ) {
    isOpen.value = false
  }
}

watch(isOpen, (val) => {
  if (val) {
    nextTick(() => updatePosition())
    window.addEventListener('scroll', updatePosition, true)
    window.addEventListener('resize', updatePosition)
  } else {
    window.removeEventListener('scroll', updatePosition, true)
    window.removeEventListener('resize', updatePosition)
  }
})

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  if (!props.disabled) {
    void fetchProjects('')
  }
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
  window.removeEventListener('scroll', updatePosition, true)
  window.removeEventListener('resize', updatePosition)
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<template>
  <div class="relative w-full">
    <button
      ref="triggerButton"
      type="button"
      class="lp-input flex w-full items-center justify-between gap-2 text-left transition"
      :class="{
        'border-[var(--lp-accent)] ring-1 ring-[var(--lp-accent)]': isOpen,
        'opacity-50 cursor-not-allowed': disabled,
      }"
      :disabled="disabled"
      @click="toggleOpen"
    >
      <div class="flex min-w-0 items-center gap-2">
        <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
          {{ selectedProject && isPrivate(selectedProject.visibility) ? 'lock' : 'public' }}
        </span>
        <span v-if="modelValue" class="truncate font-medium text-[var(--lp-text)]">
          {{ modelValue }}
        </span>
        <span v-else class="text-[var(--lp-muted)]">
          {{ loading ? t('integrations.loadingProjectsPicker') : t('integrations.searchSelectProject') }}
        </span>
      </div>
      <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
        {{ isOpen ? 'expand_less' : 'expand_more' }}
      </span>
    </button>

    <ClientOnly>
      <Teleport to="body">
        <div
          v-if="isOpen"
          ref="dropdownContainer"
          :style="dropdownStyle"
          class="max-h-72 overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl backdrop-blur-md"
        >
          <div class="border-b border-[var(--lp-line)] p-2">
            <div class="relative flex items-center">
              <span class="material-symbols-outlined absolute left-2.5 text-base text-[var(--lp-muted)]">
                search
              </span>
              <input
                :value="searchQuery"
                type="text"
                class="lp-input w-full pl-8 pr-8 text-xs"
                :placeholder="t('integrations.searchProjects')"
                @input="onSearchInput(($event.target as HTMLInputElement).value)"
              >
              <button
                v-if="searchQuery"
                type="button"
                class="absolute right-2 rounded p-0.5 text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
                @click="onSearchInput('')"
              >
                <span class="material-symbols-outlined text-xs">close</span>
              </button>
            </div>
          </div>

          <div class="max-h-56 overflow-y-auto p-1">
            <div v-if="loading && projects.length === 0" class="p-4 text-center text-xs text-[var(--lp-muted)]">
              {{ t('integrations.fetchingProjects') }}
            </div>

            <div v-else-if="searchError" class="p-3 text-xs text-[var(--lp-danger)]">
              {{ searchError }}
            </div>

            <div v-else-if="filteredProjects.length === 0" class="p-4 text-center text-xs text-[var(--lp-muted)]">
              {{ t('integrations.noProjectsMatching', { query: searchQuery }) }}
            </div>

            <button
              v-for="project in filteredProjects"
              :key="project.id"
              type="button"
              class="flex w-full items-center justify-between rounded-lg p-2.5 text-left text-xs transition"
              :class="
                modelValue === project.path_with_namespace
                  ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-text)] font-semibold'
                  : 'hover:bg-[var(--lp-panel-2)] text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
              "
              @click="selectProject(project)"
            >
              <div class="flex min-w-0 items-center gap-2.5">
                <span
                  class="material-symbols-outlined shrink-0 text-sm"
                  :class="isPrivate(project.visibility) ? 'text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
                >
                  {{ isPrivate(project.visibility) ? 'lock' : 'public' }}
                </span>
                <div class="truncate">
                  <p class="truncate font-mono text-xs text-[var(--lp-text)]">
                    {{ project.path_with_namespace }}
                  </p>
                  <p class="mt-0.5 text-[10px] text-[var(--lp-muted)]">
                    {{ project.name }} - {{ project.default_branch }}
                  </p>
                </div>
              </div>

              <span
                class="ml-2 shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide"
                :class="
                  isPrivate(project.visibility)
                    ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)]'
                    : 'bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
                "
              >
                {{ isPrivate(project.visibility) ? t('common.private') : t('common.public') }}
              </span>
            </button>
          </div>
        </div>
      </Teleport>
    </ClientOnly>
  </div>
</template>
