<script setup lang="ts">
import type {
  ContainerScaffoldConfig,
  ContainerServiceItem,
  ProjectStackOption,
  WorkspaceLinkedRepoItem,
} from '~/types/provisioning'
import type { PendingWorkspaceRepoLink, WorkspaceSourceMode } from '~/types/workspaceRepo'
import { defaultContainerServices } from '~/utils/cloudValidation'

const props = withDefaults(
  defineProps<{
    modelValue: ContainerScaffoldConfig
    disabled?: boolean
    /** When set, link/import can save immediately; otherwise link is queued. */
    workspaceId?: string | null
    launchpadProjectId?: string | null
  }>(),
  {
    disabled: false,
    workspaceId: null,
    launchpadProjectId: null,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: ContainerScaffoldConfig]
  imported: [workspaceId: string]
}>()

const pendingRepoLink = defineModel<PendingWorkspaceRepoLink | null>('pendingRepoLink', {
  default: null,
})
// Multi-repo: full staged list of linked repos (applied post-create).
const pendingRepoLinks = defineModel<WorkspaceLinkedRepoItem[]>('pendingRepoLinks', {
  default: () => [],
})

const { t } = useI18n()

const activeTab = ref<'dockerfile' | 'compose'>('dockerfile')
const previewOpen = ref(false)
const copiedState = ref(false)
// Start in the mode that matches the existing config: a workspace that already has
// scaffolded services is a "services" workspace; anything else defaults to Link.
const sourceMode = ref<WorkspaceSourceMode>(
  props.modelValue.services && props.modelValue.services.length > 0 ? 'services' : 'link',
)

const config = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const stacks: Array<{ id: ProjectStackOption; label: string; icon: string; category: 'frontend' | 'backend' }> = [
  { id: 'nextjs', label: 'Next.js (React UI)', icon: 'web', category: 'frontend' },
  { id: 'nuxtjs', label: 'Nuxt.js (Vue UI)', icon: 'web', category: 'frontend' },
  { id: 'react_vite', label: 'React (Vite SPA)', icon: 'code', category: 'frontend' },
  { id: 'vuejs', label: 'Vue.js SPA', icon: 'code', category: 'frontend' },
  { id: 'svelte', label: 'SvelteKit UI', icon: 'code', category: 'frontend' },
  { id: 'node', label: 'Node.js / Express', icon: 'javascript', category: 'backend' },
  { id: 'nestjs', label: 'NestJS (Node API)', icon: 'terminal', category: 'backend' },
  { id: 'fastapi', label: 'FastAPI (Python)', icon: 'terminal', category: 'backend' },
  { id: 'python', label: 'Python (Flask/Django)', icon: 'terminal', category: 'backend' },
  { id: 'go', label: 'Go (Golang)', icon: 'code', category: 'backend' },
  { id: 'java', label: 'Java (Spring Boot)', icon: 'coffee', category: 'backend' },
  { id: 'rust', label: 'Rust (Actix/Axum)', icon: 'memory', category: 'backend' },
  { id: 'generic', label: 'Generic (Alpine)', icon: 'data_object', category: 'backend' },
]

function initialServices(): ContainerServiceItem[] {
  if (props.modelValue.services && props.modelValue.services.length > 0) {
    return props.modelValue.services.map(s => ({ app_kind: s.app_kind || 'backend', ...s }))
  }
  return defaultContainerServices().map(s => ({ ...s }))
}

const servicesList = ref<ContainerServiceItem[]>(initialServices())

watch(
  () => props.modelValue.services,
  (newSvcs) => {
    if (newSvcs && newSvcs.length > 0) {
      servicesList.value = newSvcs.map(s => ({ app_kind: s.app_kind || 'backend', ...s }))
    }
  },
  { deep: true },
)

function frameworksFromServices(services: ContainerServiceItem[]): ContainerScaffoldConfig['frameworks'] {
  const out: NonNullable<ContainerScaffoldConfig['frameworks']> = []
  const seen = new Set<string>()
  for (const svc of services) {
    const stack = (svc.stack || '').trim()
    if (!stack || seen.has(stack)) continue
    seen.add(stack)
    out.push(stack as NonNullable<ContainerScaffoldConfig['frameworks']>[number])
  }
  return out
}

function emitServices(overrides: Partial<ContainerScaffoldConfig> = {}) {
  const frameworks = frameworksFromServices(servicesList.value)
  emit('update:modelValue', {
    ...props.modelValue,
    ...overrides,
    services: servicesList.value,
    frameworks,
    app_name: servicesList.value[0]?.name || props.modelValue.app_name,
    stack: servicesList.value[0]?.stack || frameworks[0] || props.modelValue.stack,
    listen_port: servicesList.value[0]?.listen_port || props.modelValue.listen_port,
  })
}

watch(sourceMode, (mode) => {
  if (mode === 'services') {
    if (!(servicesList.value.length > 0)) {
      servicesList.value = defaultContainerServices().map((s) => ({ ...s }))
    }
    emitServices({
      generate_dockerfile: true,
      generate_docker_compose: true,
    })
    return
  }
  // Link / Import: keep the card enabled for UI, but do not scaffold apps/*.
  servicesList.value = []
  emit('update:modelValue', {
    ...props.modelValue,
    services: [],
    frameworks: [],
    generate_dockerfile: false,
    generate_docker_compose: false,
  })
})

function updateField<K extends keyof ContainerScaffoldConfig>(key: K, val: ContainerScaffoldConfig[K]) {
  if (key === 'enabled' && val === true && !(props.modelValue.services?.length)) {
    emitServices({ enabled: true })
    return
  }
  emit('update:modelValue', {
    ...props.modelValue,
    [key]: val,
  })
}

onMounted(() => {
  if (sourceMode.value === 'services') {
    // Sync once so compose provision creates both services when Services mode is active.
    if (!(props.modelValue.services && props.modelValue.services.length > 0)) {
      emitServices()
    }
    return
  }
  // Link / Import: never scaffold apps/*. Clear any default services + generation flags
  // so a linked (or freshly created) workspace does not get a phantom apps/web-ui dir.
  if (
    props.modelValue.generate_dockerfile
    || props.modelValue.generate_docker_compose
    || (props.modelValue.services && props.modelValue.services.length > 0)
  ) {
    servicesList.value = []
    emit('update:modelValue', {
      ...props.modelValue,
      services: [],
      frameworks: [],
      generate_dockerfile: false,
      generate_docker_compose: false,
    })
  }
})


function onAppKindChange(svc: ContainerServiceItem) {
  if (svc.app_kind === 'frontend') {
    svc.stack = 'nextjs'
    svc.listen_port = 3000
    svc.expose_preview = true
    if (!svc.name || svc.name === 'api-server' || svc.name === 'app') {
      svc.name = 'web-ui'
    }
  } else {
    svc.stack = 'node'
    svc.listen_port = 8080
    if (svc.expose_preview == null) svc.expose_preview = false
    if (!svc.name || svc.name === 'web-ui' || svc.name === 'app') {
      svc.name = 'api-server'
    }
  }
  emitServices()
}

function addService() {
  const isSecond = servicesList.value.length === 1
  const appKind = isSecond && servicesList.value[0].app_kind === 'frontend' ? 'backend' : 'frontend'
  const nextNum = servicesList.value.length + 1

  servicesList.value.push({
    name: appKind === 'frontend' ? `ui-${nextNum}` : `api-${nextNum}`,
    app_kind: appKind,
    stack: appKind === 'frontend' ? 'nextjs' : 'node',
    listen_port: appKind === 'frontend' ? 3000 + nextNum : 8080 + nextNum,
    expose_preview: appKind === 'frontend',
  })
  emitServices()
}

function removeService(index: number) {
  if (servicesList.value.length <= 1) return
  servicesList.value.splice(index, 1)
  emitServices()
}

// Client-side preview generators
const previewDockerfile = computed(() => {
  const primary = servicesList.value[0] || {
    stack: config.value.stack || 'node',
    name: config.value.app_name || 'app',
    listen_port: config.value.listen_port || 8080,
    app_kind: 'backend',
  }
  const stack = primary.stack || 'node'
  const name = primary.name || 'app'
  const port = primary.listen_port || 8080

  const footer = `USER 10001:10001
EXPOSE ${port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:${port}/health || exit 1
`

  switch (stack) {
    case 'python':
    case 'fastapi':
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Python image (non-root USER 10001).

FROM python:3.12-alpine AS builder
WORKDIR /src
RUN apk add --no-cache build-base libffi-dev
COPY requirements.txt pyproject.toml poetry.lock* ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN \\
  if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; \\
  elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; \\
  else pip install --no-cache-dir uvicorn fastapi; fi

FROM python:3.12-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=${port}
${footer}CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${port}"]
# Image: ${name}`

    case 'go':
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Go image (non-root USER 10001).

FROM golang:1.23-alpine AS build
WORKDIR /src
RUN apk add --no-cache git ca-certificates
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/${name} .

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /out/${name} /app/${name}
ENV PORT=${port}
${footer}CMD ["/app/${name}"]
# Image: ${name}`

    case 'node':
    case 'nextjs':
    case 'nuxtjs':
    default:
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Node.js / Web UI image (non-root USER 10001).

FROM node:22-alpine AS deps
WORKDIR /src
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f yarn.lock ]; then yarn install --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi

FROM node:22-alpine AS build
WORKDIR /src
COPY --from=deps /src/node_modules ./node_modules
COPY . .
ENV NODE_ENV=production
RUN \\
  if [ -f package.json ] && grep -q '"build"' package.json; then \\
    npm run build || yarn build || pnpm build; \\
  else echo "no build script"; fi

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src ./
ENV NODE_ENV=production
ENV PORT=${port}
${footer}CMD ["node", "server.js"]
# Image: ${name}`
  }
})

const previewDockerCompose = computed(() => {
  const lines: string[] = ['services:']
  for (const svc of servicesList.value) {
    const sName = svc.name || 'app'
    const sPort = svc.listen_port || 8080
    lines.push(`  ${sName}:
    build:
      context: .
      dockerfile: dockers/${sName}/Dockerfile
    image: \${APP_IMAGE:-${sName}:latest}
    ports:
      - "${sPort}:${sPort}"
    environment:
      - PORT=${sPort}
    restart: unless-stopped`)
  }
  return lines.join('\n')
})

const activeContent = computed(() =>
  activeTab.value === 'dockerfile' ? previewDockerfile.value : previewDockerCompose.value,
)

const activeFileName = computed(() =>
  activeTab.value === 'dockerfile' ? 'Dockerfile' : 'docker-compose.yml',
)

async function copyToClipboard() {
  try {
    await navigator.clipboard.writeText(activeContent.value)
    copiedState.value = true
    setTimeout(() => {
      copiedState.value = false
    }, 2000)
  } catch {
    // fallback
  }
}

function downloadFile() {
  const blob = new Blob([activeContent.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = activeFileName.value
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-5 space-y-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <span class="material-symbols-outlined text-2xl text-[var(--lp-accent)]">
          deployed_code
        </span>
        <div>
          <h3 class="text-base font-semibold text-[var(--lp-text)]">
            {{ t('scaffold.containerCard.title') }}
          </h3>
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('scaffold.containerCard.blurb') }}
          </p>
        </div>
      </div>
      <label class="relative inline-flex cursor-pointer items-center">
        <input
          type="checkbox"
          class="peer sr-only"
          :checked="config.enabled"
          :disabled="disabled"
          @change="updateField('enabled', ($event.target as HTMLInputElement).checked)"
        >
        <div
          class="peer h-6 w-11 rounded-full bg-[var(--lp-line)] after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-[var(--lp-accent)] peer-checked:after:translate-x-full peer-focus:outline-none"
        />
      </label>
    </div>

    <div v-if="config.enabled" class="space-y-4 pt-2 border-t border-[var(--lp-line)]">
      <div class="flex flex-wrap gap-2 rounded-lg border border-[var(--lp-line)] p-1">
        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition min-w-[7rem]"
          :class="sourceMode === 'link' ? 'bg-[var(--lp-accent)] text-[var(--lp-on-accent)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :disabled="disabled"
          @click="sourceMode = 'link'"
        >
          <span class="material-symbols-outlined text-base">link</span>
          {{ t('scaffold.repoSource.modeLink') }}
        </button>
        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition min-w-[7rem]"
          :class="sourceMode === 'import' ? 'bg-[var(--lp-accent)] text-[var(--lp-on-accent)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :disabled="disabled"
          @click="sourceMode = 'import'"
        >
          <span class="material-symbols-outlined text-base">download</span>
          {{ t('scaffold.repoSource.modeImport') }}
        </button>
        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition min-w-[7rem]"
          :class="sourceMode === 'services' ? 'bg-[var(--lp-accent)] text-[var(--lp-on-accent)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :disabled="disabled"
          @click="sourceMode = 'services'"
        >
          <span class="material-symbols-outlined text-base">deployed_code</span>
          {{ t('scaffold.repoSource.modeServices') }}
        </button>
      </div>

      <WorkspaceRepoSourcePanel
        v-if="sourceMode === 'link' || sourceMode === 'import'"
        v-model:pending-link="pendingRepoLink"
        v-model:pending-links="pendingRepoLinks"
        :workspace-id="workspaceId"
        :force-mode="sourceMode"
        :launchpad-project-id="launchpadProjectId"
        embedded
        :disabled="disabled"
        @imported="emit('imported', $event)"
      />

      <!-- Multi-Service / Deployment Configurations -->
      <div v-if="sourceMode === 'services'" class="space-y-3">
        <div class="flex items-center justify-between">
          <span class="lp-label">{{ t('scaffold.containerCard.servicesLabel') }}</span>
          <button
            type="button"
            class="lp-btn-ghost text-xs text-[var(--lp-accent)] py-1 px-2.5 inline-flex items-center gap-1"
            :disabled="disabled"
            @click="addService"
          >
            <span class="material-symbols-outlined text-sm">add</span>
            {{ t('scaffold.containerCard.addService') }}
          </button>
        </div>

        <div class="space-y-2">
          <div
            v-for="(svc, idx) in servicesList"
            :key="idx"
            class="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/30 p-3"
          >
            <div class="w-28">
              <span class="lp-label text-[10px]">{{ t('scaffold.containerCard.serviceType') }}</span>
              <select
                v-model="svc.app_kind"
                class="lp-input py-1 text-xs font-semibold text-[var(--lp-accent)]"
                :disabled="disabled"
                @change="onAppKindChange(svc)"
              >
                <option value="frontend">{{ t('scaffold.containerCard.frontendUi') }}</option>
                <option value="backend">{{ t('scaffold.containerCard.backendApi') }}</option>
              </select>
            </div>

            <div class="flex-1 min-w-[120px]">
              <span class="lp-label text-[10px]">{{ t('scaffold.containerCard.serviceName') }}</span>
              <input
                v-model="svc.name"
                class="lp-input py-1 text-xs"
                placeholder="app"
                :disabled="disabled"
                @input="emitServices"
              >
            </div>

            <div class="w-40">
              <span class="lp-label text-[10px]">{{ t('scaffold.containerCard.frameworkStack') }}</span>
              <select
                v-model="svc.stack"
                class="lp-input py-1 text-xs"
                :disabled="disabled"
                @change="emitServices"
              >
                <option
                  v-for="st in stacks.filter(s => !svc.app_kind || s.category === svc.app_kind)"
                  :key="st.id"
                  :value="st.id"
                >
                  {{ st.label }}
                </option>
              </select>
            </div>

            <div class="w-24">
              <span class="lp-label text-[10px]">{{ t('scaffold.containerCard.port') }}</span>
              <input
                v-model.number="svc.listen_port"
                type="number"
                class="lp-input py-1 text-xs"
                placeholder="8080"
                :disabled="disabled"
                @input="emitServices"
              >
            </div>

            <label
              class="flex items-center gap-1.5 self-end pb-1 text-[11px] text-[var(--lp-muted)]"
              :title="t('scaffold.containerCard.exposePreviewHint')"
            >
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="svc.expose_preview !== false && (svc.expose_preview === true || svc.app_kind === 'frontend')"
                :disabled="disabled"
                @change="svc.expose_preview = ($event.target as HTMLInputElement).checked; emitServices()"
              >
              {{ t('scaffold.containerCard.exposePreview') }}
            </label>

            <button
              type="button"
              class="lp-btn-danger text-xs p-1.5 self-end"
              :disabled="disabled || servicesList.length <= 1"
              @click="removeService(idx)"
            >
              <span class="material-symbols-outlined text-sm">delete</span>
            </button>
          </div>
        </div>
      </div>

      <template v-if="sourceMode === 'services'">
      <p class="text-[11px] leading-relaxed text-[var(--lp-muted)]">
        {{ t('scaffold.containerCard.exposePreviewHint') }}
      </p>
      <p class="text-[11px] leading-relaxed text-[var(--lp-accent)]/90">
        {{ t('scaffold.containerCard.syncedToCicd') }}
      </p>

      <div class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-ink)]/50">
        <button
          type="button"
          class="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left transition hover:bg-[var(--lp-panel-2)]/60"
          @click="previewOpen = !previewOpen"
        >
          <div>
            <p class="text-xs font-semibold text-[var(--lp-text)]">
              {{ t('scaffold.containerCard.previewToggle') }}
            </p>
            <p class="text-[11px] text-[var(--lp-muted)]">
              {{ t('scaffold.containerCard.previewHint') }}
            </p>
          </div>
          <span
            class="material-symbols-outlined text-[var(--lp-muted)] transition-transform"
            :class="previewOpen ? 'rotate-180' : ''"
          >
            expand_more
          </span>
        </button>

        <div v-show="previewOpen" class="space-y-3 border-t border-[var(--lp-line)] px-3 pb-3 pt-3">
          <div class="flex flex-wrap gap-4">
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.generate_dockerfile"
                :disabled="disabled"
                @change="updateField('generate_dockerfile', ($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.containerCard.generateDockerfile') }}
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.generate_docker_compose"
                :disabled="disabled"
                @change="updateField('generate_docker_compose', ($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.containerCard.generateCompose') }}
            </label>
          </div>

          <div class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-ink)]/70">
            <div class="flex flex-wrap items-center justify-between border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80 px-3 py-2">
              <div class="flex items-center gap-1 font-mono text-xs">
                <button
                  type="button"
                  class="rounded px-2.5 py-1 transition"
                  :class="
                    activeTab === 'dockerfile'
                      ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)] font-semibold'
                      : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
                  "
                  @click="activeTab = 'dockerfile'"
                >
                  Dockerfile
                </button>
                <button
                  type="button"
                  class="rounded px-2.5 py-1 transition"
                  :class="
                    activeTab === 'compose'
                      ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)] font-semibold'
                      : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
                  "
                  @click="activeTab = 'compose'"
                >
                  {{ t('scaffold.containerCard.dockerCompose') }}
                </button>
              </div>

              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="inline-flex items-center gap-1.5 rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2.5 py-1 text-xs text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/50 active:scale-[0.98]"
                  @click="copyToClipboard"
                >
                  <span class="material-symbols-outlined !text-sm">
                    {{ copiedState ? 'check' : 'content_copy' }}
                  </span>
                  <span>{{ copiedState ? t('common.copied') : t('common.copy') }}</span>
                </button>

                <button
                  type="button"
                  class="inline-flex items-center gap-1.5 rounded bg-[var(--lp-accent)] px-2.5 py-1 text-xs font-medium text-[var(--lp-ink)] transition hover:opacity-90 active:scale-[0.98]"
                  @click="downloadFile"
                >
                  <span class="material-symbols-outlined !text-sm">download</span>
                  <span>{{ t('common.download') }}</span>
                </button>
              </div>
            </div>

            <pre class="max-h-64 overflow-auto p-4 font-mono text-xs leading-relaxed text-[var(--lp-text)]"><code>{{ activeContent }}</code></pre>
          </div>
        </div>
      </div>
      </template>
    </div>
  </div>
</template>
