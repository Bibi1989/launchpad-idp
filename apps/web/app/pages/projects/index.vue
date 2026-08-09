<script setup lang="ts">
import type { ProjectSummary } from '~/types/auth'

const { t } = useI18n()
const { orgs, activeOrgId } = useOrgs()
const { listProjects, createProject } = useProjects()
const { getPlan } = useBilling()

const projects = ref<ProjectSummary[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const creating = ref(false)
const name = ref('')
const planLabel = ref('free')
const limits = ref({ max_projects: 2, project_count: 0 })

const activeOrg = computed(
  () => orgs.value.find((org) => org.id === activeOrgId.value) || orgs.value[0] || null,
)
const canCreate = computed(() => {
  const role = activeOrg.value?.role
  return role === 'owner' || role === 'admin'
})

async function load() {
  loading.value = true
  error.value = null
  try {
    projects.value = await listProjects()
    if (activeOrg.value?.id) {
      const plan = await getPlan(activeOrg.value.id)
      planLabel.value = plan.plan
      limits.value = {
        max_projects: plan.max_projects,
        project_count: plan.project_count,
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.load')
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  if (!canCreate.value || creating.value || !name.value.trim()) return
  creating.value = true
  error.value = null
  try {
    await createProject({ name: name.value.trim() })
    name.value = ''
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.create')
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  void load()
})
watch(activeOrgId, () => {
  void load()
})
</script>

<template>
  <div class="w-full animate-fade-up space-y-8 pb-12">
    <header class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div class="space-y-2">
        <p class="lp-label">{{ t('projects.eyebrow') }}</p>
        <h1 class="text-3xl font-semibold tracking-tight">{{ t('projects.title') }}</h1>
        <p class="max-w-2xl text-sm text-[var(--lp-muted)]">{{ t('projects.blurb') }}</p>
        <p class="font-mono text-xs text-[var(--lp-muted)]">
          {{ t('projects.planUsage', {
            plan: planLabel,
            count: limits.project_count,
            max: limits.max_projects,
          }) }}
        </p>
      </div>
    </header>

    <section v-if="canCreate" class="lp-glass space-y-4 rounded-xl p-5">
      <h2 class="text-sm font-semibold">{{ t('projects.createTitle') }}</h2>
      <form class="lp-form-row" @submit.prevent="onCreate">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('projects.name') }}</span>
          <input v-model="name" type="text" class="lp-input" required minlength="2">
        </label>
        <button type="submit" class="lp-btn-primary" :disabled="creating">
          {{ t('projects.create') }}
        </button>
      </form>
    </section>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <AppSplash v-if="loading" compact :message="t('projects.loading')" />

    <div v-else class="grid gap-4 md:grid-cols-2">
      <NuxtLink
        v-for="project in projects"
        :key="project.id"
        :to="`/projects/${project.id}`"
        class="lp-glass block rounded-xl p-5 transition hover:border-[var(--lp-accent)]/40"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="truncate text-lg font-semibold">{{ project.name }}</h3>
            <p class="mt-1 font-mono text-xs text-[var(--lp-muted)]">{{ project.slug }}</p>
          </div>
          <span class="lp-label shrink-0">{{ project.role || '-' }}</span>
        </div>
        <p class="mt-4 text-sm text-[var(--lp-muted)]">
          {{ t('projects.workspaceCount', { count: project.workspace_count }) }}
        </p>
      </NuxtLink>
      <p v-if="!projects.length" class="text-sm text-[var(--lp-muted)] md:col-span-2">
        {{ t('projects.empty') }}
      </p>
    </div>
  </div>
</template>
