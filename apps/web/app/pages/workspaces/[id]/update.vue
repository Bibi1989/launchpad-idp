<script setup lang="ts">
import type { IaCBundleSummary } from '~/types/provisioning'

const WorkspaceServiceSetupForm = defineAsyncComponent(
  () => import('~/components/WorkspaceServiceSetupForm.vue'),
)

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { getWorkspace } = useProvisioning()

const workspaceId = computed(() => String(route.params.id))
const workspace = ref<IaCBundleSummary | null>(null)
const loading = ref(true)
const loadError = ref<string | null>(null)
const statusMessage = ref<string | null>(null)
const formError = ref<string | null>(null)

const detailPath = computed(() => `/workspaces/${workspaceId.value}`)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    workspace.value = await getWorkspace(workspaceId.value)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('workspaces.errors.load')
    workspace.value = null
  } finally {
    loading.value = false
  }
}

async function onSaved() {
  statusMessage.value = t('workspaces.update.saved')
  formError.value = null
  await load()
  await router.push({
    path: detailPath.value,
    query: { updated: '1' },
  })
}

function onError(message: string) {
  formError.value = message
  statusMessage.value = null
}

function onCancel() {
  void router.push(detailPath.value)
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="w-full space-y-8 animate-fade-up pb-12">
    <div class="space-y-3">
      <NuxtLink
        :to="detailPath"
        class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
      >
        <span class="material-symbols-outlined text-base">arrow_back</span>
        {{ t('workspaces.update.back') }}
      </NuxtLink>
      <header class="space-y-2">
        <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
          {{ t('workspaces.update.eyebrow') }}
        </p>
        <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">
          {{ t('workspaces.update.title') }}
        </h1>
        <p class="max-w-2xl text-[var(--lp-muted)]">
          {{
            workspace
              ? t('workspaces.update.blurbNamed', { name: workspace.name })
              : t('workspaces.update.blurb')
          }}
        </p>
      </header>
    </div>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">
      {{ t('workspaces.detail.loadingSetup') }}
    </p>
    <p v-else-if="loadError" class="text-sm text-[var(--lp-danger)]">{{ loadError }}</p>
    <p v-if="statusMessage" class="text-sm text-[var(--lp-ok)]">{{ statusMessage }}</p>
    <p v-if="formError" class="text-sm text-[var(--lp-danger)]">{{ formError }}</p>

    <section
      v-if="!loading && workspace"
      class="lp-glass overflow-visible rounded-xl p-5 md:p-6"
    >
      <ClientOnly>
        <WorkspaceServiceSetupForm
          :workspace-id="workspace.workspace_id"
          @saved="onSaved"
          @error="onError"
          @cancel="onCancel"
        />
        <template #fallback>
          <p class="text-sm text-[var(--lp-muted)]">{{ t('workspaces.detail.loadingSetup') }}</p>
        </template>
      </ClientOnly>
    </section>
  </div>
</template>
