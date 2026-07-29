<script setup lang="ts">
import type {
  DockerfileSeverity,
  ProjectStack,
  RegistryProvider,
} from '~/types/dockerfileSchema'
import type { GitHubInstallationItem, GitHubRepositoryItem } from '~/types/provisioning'

const { listGithubInstallations, listGithubRepositories } = useProvisioning()
const {
  loading,
  error,
  scanResult,
  selectedPath,
  editorContent,
  report,
  buildJob,
  scanRepo,
  scaffold,
  review,
  applyImprovedDockerfile,
  pushToGitHub,
  enqueueBuild,
  pollBuildJob,
  selectDetected,
  clearReport,
} = useDockerfiles()

const installations = ref<GitHubInstallationItem[]>([])
const repositories = ref<GitHubRepositoryItem[]>([])
const loadingInstalls = ref(false)
const loadingRepos = ref(false)
const busyAction = ref<string | null>(null)
const successMessage = ref<string | null>(null)

const installationId = ref<number | null>(null)
const fullName = ref('')
const branch = ref('main')
const pushPath = ref('dockers/Dockerfile')
const commitMessage = ref('chore: add Launchpad-hardened Dockerfile under dockers/')

const registryProvider = ref<RegistryProvider>('docker_hub')
const tagsInput = ref('latest')
const dockerfilePath = ref('dockers/Dockerfile')
const contextPath = ref('.')

const dockerHub = reactive({
  username: '',
  password_or_token: '',
  repository: '',
})
const awsEcr = reactive({
  access_key_id: '',
  secret_access_key: '',
  session_token: '',
  region: 'us-east-1',
  account_id: '',
  repository: '',
})
const gcpGar = reactive({
  service_account_json: '',
  project_id: '',
  location: 'us-central1',
  repository: '',
  image_name: '',
})

const stacks: Array<{ id: ProjectStack; label: string }> = [
  { id: 'node', label: 'Node.js' },
  { id: 'python', label: 'Python' },
  { id: 'go', label: 'Go' },
  { id: 'java', label: 'Java' },
  { id: 'rust', label: 'Rust' },
  { id: 'unknown', label: 'Generic' },
]

const selectedStack = ref<ProjectStack | null>(null)

const tags = computed(() =>
  tagsInput.value
    .split(/[,\s]+/)
    .map((t) => t.trim())
    .filter(Boolean),
)

const canActOnRepo = computed(
  () => Boolean(installationId.value && fullName.value.includes('/')),
)

onMounted(async () => {
  loadingInstalls.value = true
  try {
    installations.value = await listGithubInstallations()
    if (installations.value.length === 1) {
      installationId.value = installations.value[0].id
      await loadRepos()
    }
  } catch {
    installations.value = []
  } finally {
    loadingInstalls.value = false
  }
})

watch(installationId, async (id) => {
  fullName.value = ''
  repositories.value = []
  if (id) await loadRepos()
})

async function loadRepos() {
  if (!installationId.value) return
  loadingRepos.value = true
  try {
    repositories.value = await listGithubRepositories(installationId.value)
  } catch {
    repositories.value = []
  } finally {
    loadingRepos.value = false
  }
}

function severityClass(severity: DockerfileSeverity): string {
  switch (severity) {
    case 'CRITICAL':
      return 'border-red-500/40 bg-red-500/10 text-red-300'
    case 'HIGH':
      return 'border-orange-500/40 bg-orange-500/10 text-orange-200'
    case 'MEDIUM':
      return 'border-amber-500/40 bg-amber-500/10 text-amber-100'
    default:
      return 'border-[var(--lp-line)] bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
  }
}

async function onScan() {
  if (!installationId.value || !fullName.value) return
  busyAction.value = 'scan'
  successMessage.value = null
  clearReport()
  try {
    const result = await scanRepo({
      installation_id: installationId.value,
      full_name: fullName.value,
      ref: branch.value || null,
    })
    selectedStack.value = result.detected_stack
    if (result.scaffold_suggested) {
      successMessage.value = 'No Dockerfile found — scaffold a hardened one for the detected stack.'
    } else {
      successMessage.value = `Found ${result.dockerfiles.length} Dockerfile(s).`
    }
  } catch {
    /* error state set in composable */
  } finally {
    busyAction.value = null
  }
}

async function onScaffold(stack?: ProjectStack) {
  busyAction.value = 'scaffold'
  successMessage.value = null
  try {
    const result = await scaffold({
      installation_id: installationId.value,
      full_name: fullName.value || null,
      stack: stack ?? selectedStack.value,
      ref: branch.value || null,
    })
    selectedStack.value = result.stack
    pushPath.value = result.path
    dockerfilePath.value = result.path
    successMessage.value = `Scaffolded ${result.stack} Dockerfile at ${result.path}.`
  } catch {
    /* handled */
  } finally {
    busyAction.value = null
  }
}

async function onReview() {
  if (!editorContent.value.trim()) return
  busyAction.value = 'review'
  successMessage.value = null
  try {
    await review({
      dockerfile_content: editorContent.value,
      stack: selectedStack.value ?? scanResult.value?.detected_stack ?? null,
      source_path: selectedPath.value,
    })
    successMessage.value = 'Security review complete.'
  } catch {
    /* handled */
  } finally {
    busyAction.value = null
  }
}

async function onApplyImproved() {
  await applyImprovedDockerfile()
  pushPath.value = 'dockers/Dockerfile'
  dockerfilePath.value = 'dockers/Dockerfile'
  successMessage.value = 'Applied improved Dockerfile to the editor.'
}

async function onPush() {
  if (!installationId.value || !fullName.value) return
  busyAction.value = 'push'
  successMessage.value = null
  try {
    const result = await pushToGitHub({
      installation_id: installationId.value,
      full_name: fullName.value,
      dockerfile_content: editorContent.value,
      path: pushPath.value,
      commit_message: commitMessage.value,
      branch: branch.value || null,
    })
    successMessage.value = `Pushed ${result.path} to ${result.full_name}@${result.default_branch}.`
  } catch {
    /* handled */
  } finally {
    busyAction.value = null
  }
}

async function onBuild() {
  if (!installationId.value || !fullName.value || tags.value.length === 0) return
  busyAction.value = 'build'
  successMessage.value = null
  try {
    const enqueued = await enqueueBuild({
      installation_id: installationId.value,
      full_name: fullName.value,
      dockerfile_path: dockerfilePath.value,
      context_path: contextPath.value,
      branch: branch.value || null,
      tags: tags.value,
      dockerfile_content_override: editorContent.value || null,
      registry: {
        provider: registryProvider.value,
        docker_hub: registryProvider.value === 'docker_hub' ? { ...dockerHub } : null,
        aws_ecr:
          registryProvider.value === 'aws_ecr'
            ? {
                ...awsEcr,
                session_token: awsEcr.session_token || null,
              }
            : null,
        gcp_artifact_registry:
          registryProvider.value === 'gcp_artifact_registry' ? { ...gcpGar } : null,
      },
    })
    successMessage.value = `Build queued (${enqueued.job_id}). Polling status…`
    const finalJob = await pollBuildJob(enqueued.job_id)
    if (finalJob.status === 'succeeded') {
      successMessage.value = `Pushed: ${finalJob.image_refs.join(', ')}`
    } else {
      successMessage.value = null
    }
  } catch {
    /* handled */
  } finally {
    busyAction.value = null
  }
}
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-8 animate-fade-up pb-16">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        Containers
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">Dockerfiles</h1>
      <p class="max-w-2xl text-sm text-[var(--lp-muted)]">
        Scan a GitHub repository for Dockerfiles, scaffold a hardened multi-stage image when missing,
        run a Gemini security review, push under <code class="font-mono text-xs">dockers/</code>,
        then build and push to Docker Hub, AWS ECR, or GCP Artifact Registry.
      </p>
    </header>

    <section class="lp-panel space-y-4 p-5">
      <h2 class="text-base font-semibold">Repository</h2>
      <div class="grid gap-4 md:grid-cols-2">
        <label class="block space-y-1.5">
          <span class="lp-label">GitHub installation</span>
          <select
            v-model.number="installationId"
            class="lp-input"
            :disabled="loadingInstalls"
          >
            <option :value="null" disabled>
              {{ loadingInstalls ? 'Loading…' : 'Select installation' }}
            </option>
            <option
              v-for="item in installations"
              :key="item.id"
              :value="item.id"
            >
              {{ item.account_login }} ({{ item.id }})
            </option>
          </select>
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Repository</span>
          <select
            v-model="fullName"
            class="lp-input"
            :disabled="!installationId || loadingRepos"
          >
            <option value="" disabled>
              {{ loadingRepos ? 'Loading…' : 'Select repository' }}
            </option>
            <option
              v-for="repo in repositories"
              :key="repo.id"
              :value="repo.full_name"
            >
              {{ repo.full_name }}
            </option>
          </select>
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Branch / ref</span>
          <input v-model="branch" type="text" class="lp-input" placeholder="main">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Detected / scaffold stack</span>
          <select v-model="selectedStack" class="lp-input">
            <option :value="null" disabled>Auto</option>
            <option v-for="s in stacks" :key="s.id" :value="s.id">
              {{ s.label }}
            </option>
          </select>
        </label>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="lp-btn-primary text-xs uppercase tracking-wide"
          :disabled="!canActOnRepo || busyAction === 'scan'"
          @click="onScan"
        >
          {{ busyAction === 'scan' ? 'Scanning…' : 'Scan for Dockerfiles' }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide"
          :disabled="busyAction === 'scaffold'"
          @click="onScaffold()"
        >
          {{ busyAction === 'scaffold' ? 'Scaffolding…' : 'Scaffold Dockerfile' }}
        </button>
      </div>
      <p v-if="scanResult" class="text-sm text-[var(--lp-muted)]">
        Stack: <span class="text-[var(--lp-text)]">{{ scanResult.detected_stack }}</span>
        · Ref: <span class="font-mono text-xs">{{ scanResult.ref }}</span>
        · Markers:
        <span class="font-mono text-xs">{{ scanResult.root_markers.join(', ') || 'none' }}</span>
      </p>
    </section>

    <div class="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside class="lp-panel space-y-2 p-4">
        <h3 class="lp-label">Detected files</h3>
        <p v-if="!scanResult?.dockerfiles.length" class="text-xs text-[var(--lp-muted)]">
          No Dockerfiles yet. Scan a repo or scaffold one.
        </p>
        <button
          v-for="file in scanResult?.dockerfiles ?? []"
          :key="file.path"
          type="button"
          class="block w-full truncate rounded-md px-2 py-1.5 text-left font-mono text-xs transition"
          :class="selectedPath === file.path
            ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
            : 'text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]'"
          @click="selectDetected(file.path)"
        >
          {{ file.path }}
        </button>
      </aside>

      <section class="lp-panel space-y-3 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-base font-semibold">
            Editor
            <span v-if="selectedPath" class="ml-2 font-mono text-xs font-normal text-[var(--lp-muted)]">
              {{ selectedPath }}
            </span>
          </h2>
          <div class="flex flex-wrap gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs uppercase tracking-wide"
              :disabled="!editorContent.trim() || busyAction === 'review'"
              @click="onReview"
            >
              {{ busyAction === 'review' ? 'Reviewing…' : 'AI security review' }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs uppercase tracking-wide"
              :disabled="!report?.improvedDockerfile"
              @click="onApplyImproved"
            >
              Apply improved
            </button>
          </div>
        </div>
        <textarea
          v-model="editorContent"
          class="lp-input min-h-[320px] font-mono text-xs leading-relaxed"
          spellcheck="false"
          placeholder="Dockerfile content…"
        />
      </section>
    </div>

    <section v-if="report" class="lp-panel space-y-4 p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold">Security review</h2>
          <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ report.summary }}</p>
        </div>
        <span class="rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
          {{ report.analysisSource ?? 'gemini' }}
          · multi-stage {{ report.hasMultiStage ? 'yes' : 'no' }}
        </span>
      </div>
      <ul class="space-y-2">
        <li
          v-for="(issue, idx) in report.securityIssues"
          :key="`${issue.ruleId}-${idx}`"
          class="rounded-lg border px-3 py-2 text-sm"
          :class="severityClass(issue.severity)"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-mono text-[10px] uppercase tracking-wide">{{ issue.severity }}</span>
            <span class="font-mono text-xs">{{ issue.ruleId }}</span>
            <span v-if="issue.lineNumber" class="text-xs opacity-80">line {{ issue.lineNumber }}</span>
          </div>
          <p class="mt-1 opacity-90">{{ issue.description }}</p>
        </li>
      </ul>
      <div v-if="report.explanationOfChanges.length" class="space-y-1">
        <h3 class="lp-label">Changes in improved Dockerfile</h3>
        <ul class="list-disc space-y-1 pl-5 text-sm text-[var(--lp-muted)]">
          <li v-for="(change, idx) in report.explanationOfChanges" :key="idx">
            {{ change }}
          </li>
        </ul>
      </div>
    </section>

    <section class="lp-panel space-y-4 p-5">
      <h2 class="text-base font-semibold">Push to GitHub</h2>
      <p class="text-sm text-[var(--lp-muted)]">
        Commits the editor contents under <code class="font-mono text-xs">dockers/</code> via the GitHub App.
      </p>
      <div class="grid gap-4 md:grid-cols-2">
        <label class="block space-y-1.5">
          <span class="lp-label">Path</span>
          <input v-model="pushPath" type="text" class="lp-input" placeholder="dockers/Dockerfile">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Commit message</span>
          <input v-model="commitMessage" type="text" class="lp-input">
        </label>
      </div>
      <button
        type="button"
        class="lp-btn-primary text-xs uppercase tracking-wide"
        :disabled="!canActOnRepo || !editorContent.trim() || busyAction === 'push'"
        @click="onPush"
      >
        {{ busyAction === 'push' ? 'Pushing…' : 'Push Dockerfile' }}
      </button>
    </section>

    <section class="lp-panel space-y-4 p-5">
      <h2 class="text-base font-semibold">Build &amp; push image</h2>
      <p class="text-sm text-[var(--lp-muted)]">
        Clones the repo, builds with Docker, and pushes to the selected registry. Requires a running worker
        (<code class="font-mono text-xs">make worker</code> and Docker daemon.
      </p>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="provider in ([
            ['docker_hub', 'Docker Hub'],
            ['aws_ecr', 'AWS ECR'],
            ['gcp_artifact_registry', 'GCP Artifact Registry'],
          ] as const)"
          :key="provider[0]"
          type="button"
          class="rounded-md border px-3 py-1.5 text-xs uppercase tracking-wide transition"
          :class="registryProvider === provider[0]
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
            : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="registryProvider = provider[0]"
        >
          {{ provider[1] }}
        </button>
      </div>

      <div class="grid gap-4 md:grid-cols-2">
        <label class="block space-y-1.5">
          <span class="lp-label">Dockerfile path</span>
          <input v-model="dockerfilePath" type="text" class="lp-input">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Build context</span>
          <input v-model="contextPath" type="text" class="lp-input" placeholder=".">
        </label>
        <label class="block space-y-1.5 md:col-span-2">
          <span class="lp-label">Tags (comma or space separated)</span>
          <input v-model="tagsInput" type="text" class="lp-input" placeholder="latest, v1.0.0, sha-abc1234">
        </label>
      </div>

      <div v-if="registryProvider === 'docker_hub'" class="grid gap-4 md:grid-cols-2">
        <label class="block space-y-1.5">
          <span class="lp-label">Username</span>
          <input v-model="dockerHub.username" type="text" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Password / access token</span>
          <input v-model="dockerHub.password_or_token" type="password" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-1.5 md:col-span-2">
          <span class="lp-label">Repository (user/name)</span>
          <input v-model="dockerHub.repository" type="text" class="lp-input" placeholder="myorg/myapp">
        </label>
      </div>

      <div v-else-if="registryProvider === 'aws_ecr'" class="grid gap-4 md:grid-cols-2">
        <label class="block space-y-1.5">
          <span class="lp-label">Access key ID</span>
          <input v-model="awsEcr.access_key_id" type="text" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Secret access key</span>
          <input v-model="awsEcr.secret_access_key" type="password" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Session token (optional)</span>
          <input v-model="awsEcr.session_token" type="password" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Region</span>
          <input v-model="awsEcr.region" type="text" class="lp-input">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Account ID</span>
          <input v-model="awsEcr.account_id" type="text" class="lp-input" placeholder="123456789012">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">ECR repository</span>
          <input v-model="awsEcr.repository" type="text" class="lp-input" placeholder="launchpad/app">
        </label>
      </div>

      <div v-else class="grid gap-4 md:grid-cols-2">
        <label class="block space-y-1.5 md:col-span-2">
          <span class="lp-label">Service account JSON</span>
          <textarea
            v-model="gcpGar.service_account_json"
            class="lp-input min-h-[100px] font-mono text-xs"
            spellcheck="false"
            autocomplete="off"
          />
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Project ID</span>
          <input v-model="gcpGar.project_id" type="text" class="lp-input">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Location</span>
          <input v-model="gcpGar.location" type="text" class="lp-input" placeholder="us-central1">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">AR repository</span>
          <input v-model="gcpGar.repository" type="text" class="lp-input">
        </label>
        <label class="block space-y-1.5">
          <span class="lp-label">Image name</span>
          <input v-model="gcpGar.image_name" type="text" class="lp-input">
        </label>
      </div>

      <button
        type="button"
        class="lp-btn-primary text-xs uppercase tracking-wide"
        :disabled="!canActOnRepo || tags.length === 0 || busyAction === 'build'"
        @click="onBuild"
      >
        {{ busyAction === 'build' ? 'Building…' : 'Build & push' }}
      </button>

      <div v-if="buildJob" class="space-y-2 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-3">
        <p class="font-mono text-xs text-[var(--lp-muted)]">
          Job {{ buildJob.job_id }} · {{ buildJob.status }}
        </p>
        <p v-if="buildJob.error" class="text-sm text-red-300">{{ buildJob.error }}</p>
        <p v-if="buildJob.image_refs.length" class="text-sm text-[var(--lp-text)]">
          {{ buildJob.image_refs.join(', ') }}
        </p>
        <pre
          v-if="buildJob.logs.length"
          class="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-[var(--lp-muted)]"
        >{{ buildJob.logs.slice(-40).join('\n') }}</pre>
      </div>
    </section>

    <p v-if="successMessage" class="text-sm text-[var(--lp-accent)]">{{ successMessage }}</p>
    <p v-if="error" class="text-sm text-red-300">{{ error }}</p>
    <p v-if="loading && !busyAction" class="text-sm text-[var(--lp-muted)]">Working…</p>
  </div>
</template>
