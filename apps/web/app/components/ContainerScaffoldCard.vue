<script setup lang="ts">
import type { ContainerScaffoldConfig, ProjectStackOption } from '~/types/provisioning'

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

const stacks: Array<{ id: ProjectStackOption; label: string; icon: string }> = [
  { id: 'node', label: 'Node.js', icon: 'javascript' },
  { id: 'python', label: 'Python', icon: 'terminal' },
  { id: 'go', label: 'Go (Golang)', icon: 'code' },
  { id: 'java', label: 'Java', icon: 'coffee' },
  { id: 'rust', label: 'Rust', icon: 'memory' },
  { id: 'generic', label: 'Generic (Alpine)', icon: 'data_object' },
]

function updateField<K extends keyof ContainerScaffoldConfig>(key: K, val: ContainerScaffoldConfig[K]) {
  emit('update:modelValue', {
    ...props.modelValue,
    [key]: val,
  })
}

// Client-side preview generators
const previewDockerfile = computed(() => {
  const stack = config.value.stack || 'node'
  const name = config.value.app_name || 'app'
  const port = config.value.listen_port || 8080

  const footer = `USER 10001:10001
EXPOSE ${port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:${port}/health || exit 1
`

  switch (stack) {
    case 'python':
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

    case 'java':
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Java image (non-root USER 10001).

FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f mvnw ]; then chmod +x mvnw && ./mvnw -q -DskipTests package; \\
  elif [ -f gradlew ]; then chmod +x gradlew && ./gradlew -q bootJar; \\
  elif [ -f pom.xml ]; then mvn -q -DskipTests package; \\
  else echo "No Maven/Gradle build found" && exit 1; fi

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/target/*.jar /app/app.jar
ENV PORT=${port}
${footer}CMD ["java", "-jar", "/app/app.jar"]
# Image: ${name}`

    case 'rust':
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Rust image (non-root USER 10001).

FROM rust:1.83-alpine AS build
WORKDIR /src
RUN apk add --no-cache musl-dev
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo "fn main() {}" > src/main.rs && cargo build --release && rm -rf src
COPY . .
RUN cargo build --release

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/target/release/${name} /app/${name}
ENV PORT=${port}
${footer}CMD ["/app/${name}"]
# Image: ${name}`

    case 'generic':
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage generic image (non-root USER 10001).

FROM alpine:3.21 AS build
WORKDIR /src
COPY . .
RUN echo "Customize this build stage for your stack"

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src /app
ENV PORT=${port}
${footer}CMD ["sleep", "infinity"]
# Image: ${name}`

    case 'node':
    default:
      return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Node.js image (non-root USER 10001).

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
  const name = config.value.app_name || 'app'
  const port = config.value.listen_port || 8080
  return `services:
  ${name}:
    build:
      context: .
      dockerfile: dockers/Dockerfile
    image: \${APP_IMAGE:-${name}:latest}
    container_name: ${name}
    ports:
      - "${port}:${port}"
    environment:
      - PORT=${port}
      - NODE_ENV=production
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:${port}/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
`
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
            Multi-Stage Dockerfile & Docker Compose
          </h3>
          <p class="text-xs text-[var(--lp-muted)]">
            Generate hardened multi-stage Dockerfiles (USER 10001) and docker-compose.yml into workspace
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
      <!-- Options -->
      <div class="grid gap-4 sm:grid-cols-3">
        <div class="block space-y-1.5">
          <span class="lp-label">Technology Stacks &amp; Frameworks</span>
          <FrameworkMultiSelectDropdown
            :model-value="config.frameworks && config.frameworks.length > 0 ? config.frameworks : [config.stack as any]"
            :disabled="disabled"
            placeholder="Select Frameworks"
            @update:model-value="(val) => {
              updateField('frameworks', val)
              if (val.length > 0) updateField('stack', val[0] as any)
            }"
          />
        </div>

        <label class="block space-y-1.5">
          <span class="lp-label">App Name</span>
          <input
            :value="config.app_name"
            class="lp-input"
            placeholder="app"
            :disabled="disabled"
            @input="updateField('app_name', ($event.target as HTMLInputElement).value)"
          >
        </label>

        <label class="block space-y-1.5">
          <span class="lp-label">Exposed Port</span>
          <input
            :value="config.listen_port"
            type="number"
            class="lp-input"
            placeholder="8080"
            :disabled="disabled"
            @input="updateField('listen_port', Number(($event.target as HTMLInputElement).value) || 8080)"
          >
        </label>
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
          Generate Multi-Stage Dockerfile (USER 10001)
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
