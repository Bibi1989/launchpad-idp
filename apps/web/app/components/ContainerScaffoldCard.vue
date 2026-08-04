<script setup lang="ts">
import type { ContainerScaffoldConfig, ContainerServiceItem, ProjectStackOption } from '~/types/provisioning'

const props = withDefaults(
  defineProps<{
    modelValue: ContainerScaffoldConfig
    disabled?: boolean
  }>(),
  {
    disabled: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: ContainerScaffoldConfig]
}>()

const activeTab = ref<'dockerfile' | 'compose'>('dockerfile')
const copiedState = ref(false)

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

const servicesList = ref<ContainerServiceItem[]>(
  props.modelValue.services && props.modelValue.services.length > 0
    ? props.modelValue.services.map(s => ({ app_kind: s.app_kind || 'backend', ...s }))
    : [
        {
          name: props.modelValue.app_name || 'web-ui',
          app_kind: 'frontend',
          stack: 'nextjs',
          listen_port: 3000,
        },
        {
          name: 'api-server',
          app_kind: 'backend',
          stack: 'node',
          listen_port: 8080,
        },
      ],
)

watch(
  () => props.modelValue.services,
  (newSvcs) => {
    if (newSvcs && newSvcs.length > 0) {
      servicesList.value = newSvcs.map(s => ({ app_kind: s.app_kind || 'backend', ...s }))
    }
  },
  { deep: true },
)

function updateField<K extends keyof ContainerScaffoldConfig>(key: K, val: ContainerScaffoldConfig[K]) {
  emit('update:modelValue', {
    ...props.modelValue,
    [key]: val,
  })
}

function emitServices() {
  emit('update:modelValue', {
    ...props.modelValue,
    services: servicesList.value,
    app_name: servicesList.value[0]?.name || props.modelValue.app_name,
    stack: servicesList.value[0]?.stack || props.modelValue.stack,
    listen_port: servicesList.value[0]?.listen_port || props.modelValue.listen_port,
  })
}

function onAppKindChange(svc: ContainerServiceItem) {
  if (svc.app_kind === 'frontend') {
    svc.stack = 'nextjs'
    svc.listen_port = 3000
    if (!svc.name || svc.name === 'api-server' || svc.name === 'app') {
      svc.name = 'web-ui'
    }
  } else {
    svc.stack = 'node'
    svc.listen_port = 8080
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
    container_name: ${sName}
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
            Multi-Service Container Scaffold (Frontend UI &amp; Backend API)
          </h3>
          <p class="text-xs text-[var(--lp-muted)]">
            Configure frontend UI &amp; backend API deployments with hardened multi-stage Dockerfiles into workspace
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
      <!-- Multi-Service / Deployment Configurations -->
      <div class="space-y-3">
        <div class="flex items-center justify-between">
          <span class="lp-label">Services / Deployments Scaffolded</span>
          <button
            type="button"
            class="lp-btn-ghost text-xs text-[var(--lp-accent)] py-1 px-2.5 inline-flex items-center gap-1"
            :disabled="disabled"
            @click="addService"
          >
            <span class="material-symbols-outlined text-sm">add</span>
            Add Service
          </button>
        </div>

        <div class="space-y-2">
          <div
            v-for="(svc, idx) in servicesList"
            :key="idx"
            class="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/30 p-3"
          >
            <div class="w-28">
              <span class="lp-label text-[10px]">Service Type</span>
              <select
                v-model="svc.app_kind"
                class="lp-input py-1 text-xs font-semibold text-[var(--lp-accent)]"
                :disabled="disabled"
                @change="onAppKindChange(svc)"
              >
                <option value="frontend">Frontend UI</option>
                <option value="backend">Backend API</option>
              </select>
            </div>

            <div class="flex-1 min-w-[120px]">
              <span class="lp-label text-[10px]">Service Name</span>
              <input
                v-model="svc.name"
                class="lp-input py-1 text-xs"
                placeholder="app"
                :disabled="disabled"
                @input="emitServices"
              >
            </div>

            <div class="w-40">
              <span class="lp-label text-[10px]">Framework Stack</span>
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
              <span class="lp-label text-[10px]">Port</span>
              <input
                v-model.number="svc.listen_port"
                type="number"
                class="lp-input py-1 text-xs"
                placeholder="8080"
                :disabled="disabled"
                @input="emitServices"
              >
            </div>

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

      <div class="flex flex-wrap gap-4 pt-1">
        <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
          <input
            type="checkbox"
            class="accent-[var(--lp-accent)]"
            :checked="config.generate_dockerfile"
            :disabled="disabled"
            @change="updateField('generate_dockerfile', ($event.target as HTMLInputElement).checked)"
          >
          Generate Multi-Stage Dockerfiles (USER 10001)
        </label>
        <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
          <input
            type="checkbox"
            class="accent-[var(--lp-accent)]"
            :checked="config.generate_docker_compose"
            :disabled="disabled"
            @change="updateField('generate_docker_compose', ($event.target as HTMLInputElement).checked)"
          >
          Generate docker-compose.yml
        </label>
      </div>

      <!-- Preview + Copy & Download toolbar -->
      <div class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-ink)]/70">
        <!-- Tabs & Actions -->
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
              docker-compose.yml
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
              <span>{{ copiedState ? 'Copied!' : 'Copy' }}</span>
            </button>

            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded bg-[var(--lp-accent)] px-2.5 py-1 text-xs font-medium text-[var(--lp-ink)] transition hover:opacity-90 active:scale-[0.98]"
              @click="downloadFile"
            >
              <span class="material-symbols-outlined !text-sm">download</span>
              <span>Download</span>
            </button>
          </div>
        </div>

        <!-- Code Content -->
        <pre class="max-h-64 overflow-auto p-4 font-mono text-xs leading-relaxed text-[var(--lp-text)]"><code>{{ activeContent }}</code></pre>
      </div>
    </div>
  </div>
</template>
