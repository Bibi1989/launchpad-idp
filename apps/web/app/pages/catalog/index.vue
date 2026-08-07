<script setup lang="ts">
import type { GoldenPathTemplate } from '~/types/catalog'
import type { WorkspaceListItem } from '~/types/provisioning'
import { artifactModeLabel, workspaceStackLabel } from '~/utils/workspaceDisplay'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { listTemplates } = useCatalog()
const { listWorkspaces, setWorkspaceStarred } = useProvisioning()

type CatalogTab = 'templates' | 'starred'

const templates = ref<GoldenPathTemplate[]>([])
const starredWorkspaces = ref<WorkspaceListItem[]>([])
const loading = ref(true)
const errorMessage = ref<string | null>(null)
const unstarringId = ref<string | null>(null)
const confirmUnstar = ref<WorkspaceListItem | null>(null)

const activeTab = computed<CatalogTab>(() => {
  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  return tab === 'services' || tab === 'starred' ? 'starred' : 'templates'
})

function setTab(tab: CatalogTab) {
  void router.replace({
    path: '/catalog',
    query: tab === 'templates' ? {} : { tab: 'starred' },
  })
}

function requestUnstar(ws: WorkspaceListItem, event: Event) {
  event.preventDefault()
  event.stopPropagation()
  if (unstarringId.value) return
  confirmUnstar.value = ws
}

async function confirmUnstarRun() {
  const ws = confirmUnstar.value
  if (!ws || unstarringId.value) return
  unstarringId.value = ws.id
  errorMessage.value = null
  try {
    await setWorkspaceStarred(ws.id, false)
    starredWorkspaces.value = starredWorkspaces.value.filter((row) => row.id !== ws.id)
    confirmUnstar.value = null
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('catalog.errors.delete')
  } finally {
    unstarringId.value = null
  }
}

onMounted(async () => {
  loading.value = true
  errorMessage.value = null
  try {
    const [tplList, starred] = await Promise.all([
      listTemplates(),
      listWorkspaces({ starred: true }),
    ])
    templates.value = tplList
    starredWorkspaces.value = starred
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('catalog.errors.load')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="mx-auto max-w-5xl animate-fade-up space-y-8 pb-12">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p class="lp-label mb-1">{{ t('catalog.index.eyebrow') }}</p>
        <h1 class="text-2xl font-semibold tracking-tight sm:text-3xl">{{ t('catalog.index.title') }}</h1>
        <p class="mt-2 max-w-2xl text-sm text-[var(--lp-muted)]">
          {{ t('pages.catalog.blurb') }}
        </p>
      </div>
      <NuxtLink to="/catalog/create" class="lp-btn-primary text-xs uppercase tracking-wide">
        <span class="material-symbols-outlined text-base">add</span>
        {{ t('catalog.index.create') }}
      </NuxtLink>
    </header>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('catalog.index.loading') }}</p>
    <p v-else-if="errorMessage" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>

    <template v-else>
      <div
        class="inline-flex rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-1"
        role="tablist"
        :aria-label="t('catalog.index.tabsAria')"
      >
        <button
          type="button"
          role="tab"
          class="rounded-lg px-4 py-2 text-xs font-medium uppercase tracking-wide transition"
          :class="activeTab === 'templates'
            ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
            : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :aria-selected="activeTab === 'templates'"
          @click="setTab('templates')"
        >
          {{ t('catalog.index.approved') }}
          <span class="ml-1.5 font-mono text-[10px] opacity-70">{{ templates.length }}</span>
        </button>
        <button
          type="button"
          role="tab"
          class="inline-flex items-center rounded-lg px-4 py-2 text-xs font-medium uppercase tracking-wide transition"
          :class="activeTab === 'starred'
            ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
            : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :aria-selected="activeTab === 'starred'"
          @click="setTab('starred')"
        >
          <span class="material-symbols-outlined mr-1 text-sm" aria-hidden="true">star</span>
          {{ t('catalog.index.yours') }}
          <span class="ml-1.5 font-mono text-[10px] opacity-70">{{ starredWorkspaces.length }}</span>
        </button>
      </div>

      <section v-if="activeTab === 'templates'" class="space-y-4" role="tabpanel">
        <h2 class="sr-only">{{ t('catalog.index.approved') }}</h2>
        <div class="grid gap-4 sm:grid-cols-2">
          <article
            v-for="tpl in templates"
            :key="tpl.id"
            class="flex flex-col rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/40 p-4"
          >
            <div class="flex items-start justify-between gap-3">
              <div>
                <p class="font-semibold">{{ tpl.title }}</p>
                <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ tpl.description }}</p>
              </div>
              <span class="material-symbols-outlined text-[var(--lp-accent)]">{{ tpl.icon }}</span>
            </div>
            <div class="mt-4 space-y-2">
              <div>
                <p class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">{{ t('catalog.index.stacks') }}</p>
                <p class="mt-1 font-mono text-xs text-[var(--lp-text)]">{{ tpl.frameworks.join(', ') }}</p>
              </div>
              <div v-if="tpl.docker_images?.length">
                <p class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">{{ t('catalog.index.dockerImages') }}</p>
                <p class="mt-1 font-mono text-xs text-[var(--lp-text)]">{{ tpl.docker_images.join(', ') }}</p>
              </div>
            </div>
            <div class="mt-auto pt-4">
              <NuxtLink
                :to="`/catalog/create?template=${tpl.id}`"
                class="lp-btn-ghost text-xs uppercase tracking-wide"
              >
                {{ t('catalog.index.useTemplate') }}
              </NuxtLink>
            </div>
          </article>
        </div>
      </section>

      <section v-else class="space-y-4" role="tabpanel">
        <h2 class="sr-only">{{ t('catalog.index.yours') }}</h2>
        <p v-if="!starredWorkspaces.length" class="text-sm text-[var(--lp-muted)]">
          {{ t('catalog.index.empty') }}
        </p>
        <div v-else class="space-y-3">
          <div
            v-for="ws in starredWorkspaces"
            :key="ws.id"
            class="flex items-stretch gap-2 rounded-xl border border-[var(--lp-line)] transition hover:border-[var(--lp-accent)]/40 hover:bg-[var(--lp-panel-2)]/40"
          >
            <NuxtLink
              :to="`/workspaces/${ws.id}`"
              class="min-w-0 flex-1 p-4"
            >
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p class="flex items-center gap-1.5 font-semibold">
                    <span class="material-symbols-outlined text-base text-[var(--lp-accent)]" aria-hidden="true">star</span>
                    {{ ws.name }}
                  </p>
                  <p class="mt-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                    {{ workspaceStackLabel(ws) }} · {{ artifactModeLabel(ws.artifact_mode) }}
                  </p>
                </div>
              </div>
            </NuxtLink>
            <div class="flex items-center border-l border-[var(--lp-line)] px-2">
              <button
                type="button"
                class="lp-btn-ghost px-3 py-1.5 text-[10px] uppercase tracking-wide"
                :disabled="unstarringId === ws.id"
                :title="t('catalog.index.unstar', { name: ws.name })"
                @click="requestUnstar(ws, $event)"
              >
                <span class="material-symbols-outlined text-sm">star</span>
                {{ unstarringId === ws.id ? '…' : t('catalog.index.unstarAction') }}
              </button>
            </div>
          </div>
        </div>
      </section>
    </template>

    <ConfirmDialog
      :open="confirmUnstar !== null"
      :title="t('catalog.detail.deleteTitle')"
      :message="confirmUnstar
        ? t('catalog.index.deleteConfirm', { name: confirmUnstar.name })
        : ''"
      :confirm-label="t('catalog.index.unstarAction')"
      :cancel-label="t('common.cancel')"
      :busy="unstarringId !== null"
      @update:open="(value) => { if (!value) confirmUnstar = null }"
      @confirm="confirmUnstarRun"
    />
  </div>
</template>
