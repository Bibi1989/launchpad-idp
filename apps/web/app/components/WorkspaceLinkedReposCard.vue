<script setup lang="ts">
/**
 * Read-only display of the repositories linked to a workspace (link or import),
 * for the workspace detail page. Editing happens on the update page.
 */
import type { WorkspaceLinkedRepoItem } from '~/types/provisioning'

const props = defineProps<{ workspaceId: string }>()

const { t } = useI18n()
const { getWorkspaceLinkedRepos } = useProvisioning()

const repos = ref<WorkspaceLinkedRepoItem[]>([])
const loading = ref(false)

async function load() {
  if (!props.workspaceId) return
  loading.value = true
  try {
    repos.value = (await getWorkspaceLinkedRepos(props.workspaceId)).repos
  } catch {
    repos.value = []
  } finally {
    loading.value = false
  }
}

function repoLabel(repo: WorkspaceLinkedRepoItem): string {
  return (
    repo.full_name
    || repo.git_repo_url.replace(/^https?:\/\//i, '').replace(/\.git$/i, '')
  )
}

/** Provider from the actual URL host, not the stored kind (which can be wrong). */
function providerLabel(repo: WorkspaceLinkedRepoItem): string {
  const url = (repo.git_repo_url || '').toLowerCase()
  if (url.includes('gitlab')) return 'gitlab'
  if (url.includes('github')) return 'github'
  return repo.kind
}

/** The primary repo is the flagged one, else the first (server marks exactly one). */
function isPrimary(repo: WorkspaceLinkedRepoItem, index: number): boolean {
  const anyFlagged = repos.value.some((r) => r.primary)
  return anyFlagged ? Boolean(repo.primary) : index === 0
}

onMounted(load)
watch(() => props.workspaceId, load)
</script>

<template>
  <section class="lp-glass rounded-xl p-5">
    <div class="mb-3 flex items-center justify-between gap-3">
      <p class="flex items-center gap-2 text-sm font-semibold text-[var(--lp-text)]">
        <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">hub</span>
        {{ t('scaffold.repoSource.linkedTitle') }}
      </p>
      <NuxtLink
        :to="`/workspaces/${workspaceId}/update`"
        class="text-xs text-[var(--lp-accent)] hover:underline"
      >
        {{ t('scaffold.repoSource.manage') }}
      </NuxtLink>
    </div>

    <p v-if="loading" class="text-xs text-[var(--lp-muted)]">{{ t('common.loading') }}</p>

    <p v-else-if="!repos.length" class="text-xs text-[var(--lp-muted)]">
      {{ t('scaffold.repoSource.emptyDetail') }}
    </p>

    <ul v-else class="space-y-1">
      <li
        v-for="(repo, i) in repos"
        :key="repo.git_repo_url"
        class="flex items-center justify-between gap-2 rounded-md border border-[var(--lp-line)] px-3 py-1.5"
      >
        <span class="flex min-w-0 items-center gap-2">
          <span
            class="rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wide"
            :class="providerLabel(repo) === 'gitlab'
              ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
              : 'bg-[var(--lp-line)] text-[var(--lp-muted)]'"
          >{{ providerLabel(repo) }}</span>
          <span class="truncate font-mono text-xs text-[var(--lp-text)]">{{ repoLabel(repo) }}</span>
          <span class="shrink-0 text-[10px] text-[var(--lp-muted)]">@ {{ repo.git_branch }}</span>
        </span>
        <span
          v-if="isPrimary(repo, i)"
          class="shrink-0 rounded bg-[var(--lp-accent)]/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-[var(--lp-accent)]"
        >
          {{ t('scaffold.repoSource.primary') }}
        </span>
      </li>
    </ul>
  </section>
</template>
