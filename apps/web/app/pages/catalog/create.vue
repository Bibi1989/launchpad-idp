<script setup lang="ts">
import type { GoldenPathTemplate, ServiceTier } from '~/types/catalog'
import type { GitHubAppStatus } from '~/types/provisioning'

const route = useRoute()
const { listTemplates, createService } = useCatalog()
const { getGithubAppStatus } = useProvisioning()

const templates = ref<GoldenPathTemplate[]>([])
const githubApp = ref<GitHubAppStatus | null>(null)
const loading = ref(true)
const submitting = ref(false)
const errorMessage = ref<string | null>(null)

const form = reactive({
  name: '',
  description: '',
  template_id: '',
  owner: '',
  tier: 'tier-2' as ServiceTier,
  slo_target: '99.5',
  runbook_url: '',
  on_call: '',
  vcs_provider: 'github' as 'none' | 'github' | 'gitlab',
  create_github_repo: false,
  github_installation_id: null as number | null,
  github_organization: '',
  github_private: true,
  gitlab_project_name: '',
  gitlab_private: true,
  enforce_scorecard_gate: true,
  trigger_initial_preview: false,
})

const selectedTemplate = computed(() =>
  templates.value.find((t) => t.id === form.template_id) ?? null,
)

const selectedInstallation = computed(() =>
  githubApp.value?.installations?.find((inst) => inst.id === form.github_installation_id) ?? null,
)

const isPersonalGithubAccount = computed(() => {
  const type = selectedInstallation.value?.account_type?.toLowerCase() ?? ''
  return type === 'user' || type === 'personal'
})

const githubNewRepoUrl = computed(() => {
  const name = form.name.trim() || 'your-service'
  return `https://github.com/new?name=${encodeURIComponent(name)}`
})

watch(selectedTemplate, (tpl) => {
  if (!tpl) return
  form.tier = tpl.default_tier
  form.slo_target = tpl.default_slo
})

onMounted(async () => {
  loading.value = true
  try {
    templates.value = await listTemplates()
    const q = typeof route.query.template === 'string' ? route.query.template : ''
    form.template_id = q && templates.value.some((t) => t.id === q)
      ? q
      : (templates.value[0]?.id ?? '')
    try {
      githubApp.value = await getGithubAppStatus()
      const first = githubApp.value?.installations?.[0]
      if (first) form.github_installation_id = first.id
    } catch {
      githubApp.value = null
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load templates'
  } finally {
    loading.value = false
  }
})

async function onSubmit() {
  if (submitting.value) return
  submitting.value = true
  errorMessage.value = null
  try {
    const created = await createService({
      name: form.name.trim().toLowerCase().replace(/_/g, '-'),
      description: form.description,
      template_id: form.template_id,
      owner: form.owner,
      tier: form.tier,
      slo_target: form.slo_target,
      runbook_url: form.runbook_url || null,
      on_call: form.on_call || null,
      vcs_provider: form.vcs_provider,
      create_github_repo: form.vcs_provider === 'github' && form.create_github_repo,
      github_installation_id: (form.vcs_provider === 'github' && form.create_github_repo) ? form.github_installation_id : null,
      github_organization: form.github_organization || null,
      github_private: form.github_private,
      gitlab_project_name: form.gitlab_project_name || null,
      gitlab_private: form.gitlab_private,
      enforce_scorecard_gate: form.enforce_scorecard_gate,
      trigger_initial_preview: form.trigger_initial_preview,
    })
    await navigateTo(`/catalog/${created.id}`)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to create service'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-2xl animate-fade-up space-y-6 pb-12">
    <header>
      <NuxtLink to="/catalog" class="font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] hover:text-[var(--lp-text)]">
        ← Catalog
      </NuxtLink>
      <h1 class="mt-3 text-2xl font-semibold">Create service</h1>
      <p class="mt-2 text-sm text-[var(--lp-muted)]">
        Scaffolds Dockerfile, Kubernetes manifests, CI security scans, and a workspace from an approved golden path template.
      </p>
    </header>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Loading…</p>
    <form v-else class="space-y-5 rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/70 p-6" @submit.prevent="onSubmit">
      <label class="block space-y-2">
        <span class="lp-label">Golden path template</span>
        <select v-model="form.template_id" class="lp-input" required>
          <option v-for="tpl in templates" :key="tpl.id" :value="tpl.id">
            {{ tpl.title }} (v{{ tpl.version }})
          </option>
        </select>
        <p v-if="selectedTemplate" class="text-xs text-[var(--lp-muted)]">{{ selectedTemplate.description }}</p>
        <div v-if="selectedTemplate" class="mt-2 space-y-2">
          <div v-if="selectedTemplate.frameworks.length" class="flex flex-wrap items-center gap-1.5">
            <span class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">Stacks</span>
            <span
              v-for="fw in selectedTemplate.frameworks"
              :key="fw"
              class="rounded border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-2 py-0.5 font-mono text-[10px] text-[var(--lp-accent)]"
            >
              {{ fw }}
            </span>
          </div>
          <div v-if="selectedTemplate.docker_images?.length" class="flex flex-wrap items-center gap-1.5">
            <span class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">Images</span>
            <span
              v-for="image in selectedTemplate.docker_images"
              :key="image"
              class="rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] text-[var(--lp-text)]"
            >
              {{ image }}
            </span>
          </div>
        </div>
      </label>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">Service name</span>
          <input v-model="form.name" class="lp-input" placeholder="payments-api" required pattern="[a-z][a-z0-9-]*">
          <p class="text-xs text-[var(--lp-muted)]">
            Also used as the repository name when creating or pushing a repo.
          </p>
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Owner (team / email)</span>
          <input v-model="form.owner" class="lp-input" placeholder="platform@company.com" required>
        </label>
      </div>

      <label class="block space-y-2">
        <span class="lp-label">Description</span>
        <textarea v-model="form.description" rows="2" class="lp-input" placeholder="What this service does" />
      </label>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">Tier</span>
          <select v-model="form.tier" class="lp-input">
            <option value="critical">critical</option>
            <option value="tier-1">tier-1</option>
            <option value="tier-2">tier-2</option>
            <option value="tier-3">tier-3</option>
          </select>
        </label>
        <label class="block space-y-2">
          <span class="lp-label">SLO target %</span>
          <input v-model="form.slo_target" class="lp-input" placeholder="99.5">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Runbook URL</span>
          <input v-model="form.runbook_url" class="lp-input" placeholder="https://…">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">On-call</span>
          <input v-model="form.on_call" class="lp-input" placeholder="pagerduty-team">
        </label>
      </div>

      <!-- VCS Repository Setup -->
      <div class="space-y-3 rounded-xl border border-[var(--lp-line)] p-4">
        <p class="lp-label">Version Control Integration</p>
        <div class="grid gap-3 sm:grid-cols-3">
          <label class="flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--lp-line)] p-3 text-xs" :class="{ 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/5': form.vcs_provider === 'github' }">
            <input v-model="form.vcs_provider" type="radio" value="github" class="accent-[var(--lp-accent)]">
            <span>GitHub</span>
          </label>
          <label class="flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--lp-line)] p-3 text-xs" :class="{ 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/5': form.vcs_provider === 'gitlab' }">
            <input v-model="form.vcs_provider" type="radio" value="gitlab" class="accent-[var(--lp-accent)]">
            <span>GitLab</span>
          </label>
          <label class="flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--lp-line)] p-3 text-xs" :class="{ 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/5': form.vcs_provider === 'none' }">
            <input v-model="form.vcs_provider" type="radio" value="none" class="accent-[var(--lp-accent)]">
            <span>None (Local IaC only)</span>
          </label>
        </div>

        <template v-if="form.vcs_provider === 'github'">
          <label class="flex items-center gap-2 text-sm mt-2">
            <input v-model="form.create_github_repo" type="checkbox" class="accent-[var(--lp-accent)]">
            Create / push GitHub repository
          </label>
          <template v-if="form.create_github_repo">
            <label class="block space-y-2">
              <span class="lp-label">GitHub installation</span>
              <select v-model.number="form.github_installation_id" class="lp-input" required>
                <option
                  v-for="inst in githubApp?.installations ?? []"
                  :key="inst.id"
                  :value="inst.id"
                >
                  {{ inst.account_login }} ({{ inst.account_type }})
                </option>
              </select>
            </label>
            <p
              v-if="isPersonalGithubAccount"
              class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2 text-xs leading-5 text-[var(--lp-muted)]"
            >
              Personal GitHub accounts can’t create new repos via App installation tokens.
              Create an empty repo named
              <span class="font-mono text-[var(--lp-text)]">{{ form.name.trim() || 'your-service' }}</span>
              first
              (<a :href="githubNewRepoUrl" class="text-[var(--lp-accent)] hover:underline" target="_blank" rel="noreferrer">open GitHub</a>),
              grant the App access to it, then retry Create. Organization installs can create repos directly.
            </p>
            <label class="flex items-center gap-2 text-sm">
              <input v-model="form.github_private" type="checkbox" class="accent-[var(--lp-accent)]">
              Private repository
            </label>
          </template>
        </template>

        <template v-if="form.vcs_provider === 'gitlab'">
          <div class="space-y-3 pt-2">
            <label class="block space-y-2">
              <span class="lp-label">GitLab Project Name</span>
              <input v-model="form.gitlab_project_name" class="lp-input" :placeholder="form.name || 'my-service'">
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input v-model="form.gitlab_private" type="checkbox" class="accent-[var(--lp-accent)]">
              Private project
            </label>
          </div>
        </template>
      </div>

      <!-- Governance & Options -->
      <div class="space-y-3 rounded-xl border border-[var(--lp-line)] p-4">
        <p class="lp-label">Quality & Governance</p>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.enforce_scorecard_gate" type="checkbox" class="accent-[var(--lp-accent)]">
          <span class="font-medium">Scorecard Hard Gate (Require 70/100 pass)</span>
        </label>
        <p class="text-xs text-[var(--lp-muted)]">
          Validates non-root Dockerfile, SAST/container security scanning in CI, and Kubernetes resource requests/limits.
        </p>

        <label class="flex items-center gap-2 text-sm pt-2">
          <input v-model="form.trigger_initial_preview" type="checkbox" class="accent-[var(--lp-accent)]">
          <span class="font-medium">Deploy initial PR preview environment</span>
        </label>
        <p class="text-xs text-[var(--lp-muted)]">
          Immediately provisions an active preview deployment upon service creation.
        </p>
      </div>

      <p v-if="errorMessage" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>

      <div class="flex flex-wrap gap-2">
        <NuxtLink to="/catalog" class="lp-btn-ghost text-xs uppercase tracking-wide">
          Cancel
        </NuxtLink>
        <button type="submit" class="lp-btn-primary flex-1 text-xs uppercase tracking-wide" :disabled="submitting">
          {{ submitting ? 'Creating…' : 'Create service' }}
        </button>
      </div>
    </form>
  </div>
</template>
