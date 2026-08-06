<script setup lang="ts">
import type { GitHubRepositoryItem, GitHubRepositorySearchItem } from '~/types/provisioning'

const props = withDefaults(
  defineProps<{
    modelValue: string
    installationId?: number | null
    disabled?: boolean
  }>(),
  {
    installationId: null,
    disabled: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  selectRepo: [repo: GitHubRepositorySearchItem | GitHubRepositoryItem]
}>()

const { searchGithubRepositories } = useProvisioning()
const { t } = useI18n()

const searchQuery = ref('')
const isOpen = ref(false)
const loading = ref(false)
const repos = ref<GitHubRepositorySearchItem[]>([])
const searchError = ref<string | null>(null)

const triggerButton = ref<HTMLElement | null>(null)
const dropdownContainer = ref<HTMLElement | null>(null)
const dropdownStyle = ref<Record<string, string>>({})

const filteredRepos = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return repos.value
  return repos.value.filter(
    (r) =>
      r.name.toLowerCase().includes(q) ||
      r.fullName.toLowerCase().includes(q) ||
      r.owner.toLowerCase().includes(q),
  )
})

const selectedRepo = computed(() =>
  repos.value.find((r) => r.fullName === props.modelValue) || null,
)

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

async function fetchRepos(query = '') {
  loading.value = true
  searchError.value = null
  try {
    const res = await searchGithubRepositories({
      q: query || undefined,
      installationId: props.installationId,
      perPage: 100,
    })
    repos.value = res.repositories
  } catch (err) {
    searchError.value = err instanceof Error ? err.message : 'Failed to search repositories'
  } finally {
    loading.value = false
  }
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null

function onSearchInput(val: string) {
  searchQuery.value = val
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    void fetchRepos(searchQuery.value)
  }, 300)
}

function selectRepository(repo: GitHubRepositorySearchItem) {
  emit('update:modelValue', repo.fullName)
  emit('selectRepo', repo)
  isOpen.value = false
}

function toggleOpen() {
  if (props.disabled) return
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    nextTick(() => updatePosition())
    if (repos.value.length === 0) {
      void fetchRepos(searchQuery.value)
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

watch(
  () => props.installationId,
  () => {
    repos.value = []
    searchQuery.value = ''
    searchError.value = null
    if (props.modelValue) {
      emit('update:modelValue', '')
    }
    if (props.installationId && isOpen.value) {
      void fetchRepos('')
    }
  },
)

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  if (props.installationId) {
    void fetchRepos('')
  }
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
  window.removeEventListener('scroll', updatePosition, true)
  window.removeEventListener('resize', updatePosition)
})
</script>

<template>
  <div class="relative w-full">
    <!-- Trigger Button -->
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
          {{ selectedRepo?.isPrivate ? 'lock' : 'public' }}
        </span>
        <span v-if="modelValue" class="truncate font-medium text-[var(--lp-text)]">
          {{ modelValue }}
        </span>
        <span v-else class="text-[var(--lp-muted)]">
          {{ loading ? t('integrations.loadingReposPicker') : t('integrations.searchSelectRepo') }}
        </span>
      </div>
      <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
        {{ isOpen ? 'expand_less' : 'expand_more' }}
      </span>
    </button>

    <!-- Dropdown Panel Teleported to body to avoid overflow clipping -->
    <ClientOnly>
      <Teleport to="body">
        <div
          v-if="isOpen"
          ref="dropdownContainer"
          :style="dropdownStyle"
          class="max-h-72 overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl backdrop-blur-md"
        >
          <!-- Search Input -->
          <div class="border-b border-[var(--lp-line)] p-2">
            <div class="relative flex items-center">
              <span class="material-symbols-outlined absolute left-2.5 text-base text-[var(--lp-muted)]">
                search
              </span>
              <input
                :value="searchQuery"
                type="text"
                class="lp-input w-full pl-8 pr-8 text-xs"
                :placeholder="t('integrations.searchRepos')"
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

          <!-- Repo List -->
          <div class="max-h-56 overflow-y-auto p-1">
            <div v-if="loading && repos.length === 0" class="p-4 text-center text-xs text-[var(--lp-muted)]">
              {{ t('integrations.fetchingRepos') }}
            </div>

            <div v-else-if="searchError" class="p-3 text-xs text-[var(--lp-danger)]">
              {{ searchError }}
            </div>

            <div v-else-if="filteredRepos.length === 0" class="p-4 text-center text-xs text-[var(--lp-muted)]">
              {{ t('integrations.noReposMatching', { query: searchQuery }) }}
            </div>

            <button
              v-for="repo in filteredRepos"
              :key="repo.id"
              type="button"
              class="flex w-full items-center justify-between rounded-lg p-2.5 text-left text-xs transition"
              :class="
                modelValue === repo.fullName
                  ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-text)] font-semibold'
                  : 'hover:bg-[var(--lp-panel-2)] text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
              "
              @click="selectRepository(repo)"
            >
              <div class="flex min-w-0 items-center gap-2.5">
                <span
                  class="material-symbols-outlined text-sm shrink-0"
                  :class="repo.isPrivate ? 'text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
                >
                  {{ repo.isPrivate ? 'lock' : 'public' }}
                </span>
                <div class="truncate">
                  <p class="truncate font-mono text-xs text-[var(--lp-text)]">
                    {{ repo.fullName }}
                  </p>
                  <p class="mt-0.5 text-[10px] text-[var(--lp-muted)]">
                    {{ repo.owner }} · {{ repo.defaultBranch }}
                  </p>
                </div>
              </div>

              <span
                class="ml-2 shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide"
                :class="repo.isPrivate ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)]' : 'bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'"
              >
                {{ repo.isPrivate ? t('common.private') : t('common.public') }}
              </span>
            </button>
          </div>
        </div>
      </Teleport>
    </ClientOnly>
  </div>
</template>
