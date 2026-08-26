import type {
  CicdPlatform,
  ContainerScaffoldConfig,
  FrameworkOption,
  InfraGenerationConfig,
  K8sScaffoldMode,
  KubernetesPackaging,
  ProvisionEngine,
  WorkspaceArtifactsMode,
} from '~/types/provisioning'
import { FRAMEWORK_OPTIONS } from '~/types/provisioning'
import { buildAnsibleScaffold } from '~/utils/ansibleScaffold'
import {
  defaultCicdSecurityConfig,
  renderCicdWorkflow,
  type CicdSecurityConfig,
} from '~/utils/cicdWorkflowGenerator'
import { defaultAnsibleConfig } from '~/utils/cloudValidation'

const FRAMEWORK_IDS_BY_LENGTH = (
  [
    ...FRAMEWORK_OPTIONS.map((item) => item.id),
    'node',
    'python',
    'java',
  ] as FrameworkOption[]
).sort((a, b) => b.length - a.length)

const DEFAULT_LISTEN_PORTS: Partial<Record<FrameworkOption, number>> = {
  react_vite: 80,
  nextjs: 3000,
  nuxtjs: 3000,
  vuejs: 80,
  svelte: 3000,
  fastapi: 8000,
  flask: 5000,
  django: 8000,
  express: 3000,
  nestjs: 3000,
  springboot: 8080,
  go: 8080,
  rust: 8080,
  node: 3000,
  python: 8000,
  java: 8080,
  generic: 8080,
}

export function defaultInfraGenerationConfig(
  opts: { isLocal?: boolean } = {},
): InfraGenerationConfig {
  if (opts.isLocal) {
    return {
      provision: { enabled: false, engine: 'terraform' },
      kubernetes: { enabled: true, mode: 'k8s' },
      cicd: {
        enabled: false,
        platform: 'github',
        security: defaultCicdSecurityConfig(),
        frameworks: [],
      },
    }
  }
  return {
    provision: { enabled: true, engine: 'launchpad' },
    kubernetes: { enabled: false, mode: 'k8s' },
    cicd: {
      enabled: false,
      platform: 'github',
      security: defaultCicdSecurityConfig(),
      frameworks: [],
    },
  }
}

export function infraConfigToArtifactMode(
  config: InfraGenerationConfig,
): WorkspaceArtifactsMode {
  const hasProvision = config.provision.enabled
  const hasKubernetes = config.kubernetes.enabled
  if (hasProvision && hasKubernetes) return 'both'
  if (hasProvision) return 'iac_only'
  if (hasKubernetes) return 'manifest_only'
  // Nothing selected: still request IaC so cloud apply has a tree to run
  // (Provision toggle defaults on for cloud; this is a safe fallback).
  return 'iac_only'
}

export function infraConfigToKubernetesPackaging(
  config: InfraGenerationConfig,
): KubernetesPackaging {
  if (!config.kubernetes.enabled) return 'none'
  if (config.kubernetes.mode === 'helm') return 'helm'
  if (config.kubernetes.mode === 'kustomize') return 'kustomize'
  return 'raw_manifests'
}

export function artifactModeToInfraConfig(
  artifactMode: WorkspaceArtifactsMode,
  engine: ProvisionEngine = 'terraform',
  packaging: KubernetesPackaging = 'none',
  cicdPlatform: CicdPlatform = 'github',
): InfraGenerationConfig {
  const hasProvision = artifactMode === 'iac_only' || artifactMode === 'both'
  const hasKubernetes = artifactMode === 'manifest_only' || artifactMode === 'both'
  return {
    provision: { enabled: hasProvision, engine },
    kubernetes: {
      enabled: hasKubernetes,
      mode: packaging === 'helm' ? 'helm' : packaging === 'kustomize' ? 'kustomize' : 'k8s',
    },
    cicd: {
      enabled: false,
      platform: cicdPlatform,
      security: defaultCicdSecurityConfig(),
      frameworks: [],
    },
  }
}

interface ScaffoldTarget {
  path: string
  content: string
}

export function buildProvisionScaffold(
  workspaceId: string,
  workspaceName: string,
  engine: ProvisionEngine,
): ScaffoldTarget[] {
  if (engine === 'terraform' || engine === 'opentofu') {
    const tool = engine === 'opentofu' ? 'OpenTofu' : 'Terraform'
    return [
      {
        path: 'infra/terraform/main.tf',
        content: [
          '# Generated for ' + tool + ' (HCL-compatible)',
          'terraform {',
          '  required_version = ">= 1.6.0"',
          '}',
          '',
          'locals {',
          `  workspace_id = "${workspaceId}"`,
          '}',
          '',
          'output "workspace_id" {',
          '  value = local.workspace_id',
          '}',
          '',
        ].join('\n'),
      },
    ]
  }
  if (engine === 'ansible') {
    return buildAnsibleScaffold(workspaceName || 'launchpad-workspace', {
      ...defaultAnsibleConfig(),
      enabled: true,
    })
  }
  return [
    {
      path: 'infra/pulumi/Pulumi.yaml',
      content: [
        `name: ${workspaceName || 'launchpad-workspace'}`,
        'runtime: nodejs',
        'description: Launchpad workspace Pulumi stack',
        '',
      ].join('\n'),
    },
    {
      path: 'infra/pulumi/index.ts',
      content: [
        'import * as pulumi from "@pulumi/pulumi";',
        '',
        'const cfg = new pulumi.Config();',
        'export const workspace = cfg.get("workspace") ?? "launchpad";',
        '',
      ].join('\n'),
    },
  ]
}

export function buildKubernetesScaffold(mode: K8sScaffoldMode): ScaffoldTarget[] {
  if (mode === 'kustomize') {
    return [
      {
        path: 'infra/kustomize/base/kustomization.yaml',
        content: [
          'apiVersion: kustomize.config.k8s.io/v1beta1',
          'kind: Kustomization',
          'resources:',
          '  - namespace.yaml',
          '  - deployment.yaml',
          '  - service.yaml',
          '',
        ].join('\n'),
      },
      {
        path: 'infra/kustomize/base/namespace.yaml',
        content: ['apiVersion: v1', 'kind: Namespace', 'metadata:', '  name: lp-app', ''].join('\n'),
      },
      {
        path: 'infra/kustomize/base/deployment.yaml',
        content: [
          'apiVersion: apps/v1',
          'kind: Deployment',
          'metadata:',
          '  name: app',
          '  namespace: lp-app',
          'spec:',
          '  replicas: 1',
          '  selector:',
          '    matchLabels:',
          '      app: app',
          '  template:',
          '    metadata:',
          '      labels:',
          '        app: app',
          '    spec:',
          '      containers:',
          '        - name: app',
          '          image: app:latest',
          '          ports:',
          '            - containerPort: 80',
          '',
        ].join('\n'),
      },
      {
        path: 'infra/kustomize/base/service.yaml',
        content: [
          'apiVersion: v1',
          'kind: Service',
          'metadata:',
          '  name: app',
          '  namespace: lp-app',
          'spec:',
          '  selector:',
          '    app: app',
          '  ports:',
          '    - port: 80',
          '      targetPort: 80',
          '',
        ].join('\n'),
      },
      {
        path: 'infra/kustomize/overlays/prod/kustomization.yaml',
        content: [
          'apiVersion: kustomize.config.k8s.io/v1beta1',
          'kind: Kustomization',
          'resources:',
          '  - ../../base',
          'namePrefix: prod-',
          '',
        ].join('\n'),
      },
    ]
  }
  if (mode === 'k8s') {
    return [
      {
        path: 'infra/k8s/manifests/namespace.yaml',
        content: ['apiVersion: v1', 'kind: Namespace', 'metadata:', '  name: lp-app', ''].join('\n'),
      },
      {
        path: 'infra/k8s/manifests/deployment.yaml',
        content: [
          'apiVersion: apps/v1',
          'kind: Deployment',
          'metadata:',
          '  name: app',
          '  namespace: lp-app',
          'spec:',
          '  replicas: 1',
          '  selector:',
          '    matchLabels:',
          '      app: app',
          '  template:',
          '    metadata:',
          '      labels:',
          '        app: app',
          '    spec:',
          '      containers:',
          '        - name: app',
          '          image: app:latest',
          '          ports:',
          '            - containerPort: 80',
          '',
        ].join('\n'),
      },
      {
        path: 'infra/k8s/manifests/service.yaml',
        content: [
          'apiVersion: v1',
          'kind: Service',
          'metadata:',
          '  name: app',
          '  namespace: lp-app',
          'spec:',
          '  selector:',
          '    app: app',
          '  ports:',
          '    - port: 80',
          '      targetPort: 80',
          '',
        ].join('\n'),
      },
    ]
  }
  return [
    {
      path: 'infra/helm/app-chart/Chart.yaml',
      content: ['apiVersion: v2', 'name: app-chart', 'version: 0.1.0', ''].join('\n'),
    },
    {
      path: 'infra/helm/app-chart/values.yaml',
      content: ['replicaCount: 1', 'image:', '  repository: app', '  tag: "latest"', ''].join('\n'),
    },
    {
      path: 'infra/helm/app-chart/templates/deployment.yaml',
      content: [
        'apiVersion: apps/v1',
        'kind: Deployment',
        'metadata:',
        '  name: app',
        'spec:',
        '  replicas: {{ .Values.replicaCount }}',
        '  selector:',
        '    matchLabels:',
        '      app: app',
        '  template:',
        '    metadata:',
        '      labels:',
        '        app: app',
        '    spec:',
        '      containers:',
        '        - name: app',
        '          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"',
        '',
      ].join('\n'),
    },
  ]
}

function sanitizeServiceSlug(stack: string): string {
  return stack.replace(/_/g, '-').toLowerCase()
}

function sanitizeAppName(name: string): string {
  const cleaned = name.trim().replace(/[^a-zA-Z0-9._-]+/g, '-') || 'app'
  return cleaned.slice(0, 64)
}

/** Meaningful Dockerfile path under `dockers/` for scaffolded services. */
export function dockerfilePathForService(
  appName: string,
  stack?: FrameworkOption,
  multi = false,
): string {
  const appSlug = sanitizeAppName(appName)
  if (multi && stack) {
    return `dockers/Dockerfile.${appSlug}-${sanitizeServiceSlug(stack)}`
  }
  return `dockers/Dockerfile.${appSlug}`
}

function defaultListenPortForStack(stack: FrameworkOption, fallback: number): number {
  return DEFAULT_LISTEN_PORTS[stack] ?? fallback
}

function resolveDockerStacks(cfg: ContainerScaffoldConfig): FrameworkOption[] {
  const frameworks = cfg.frameworks ?? []
  if (frameworks.length > 0) return [...frameworks]
  return [cfg.stack || 'node']
}

/** Hardened multi-stage Dockerfile content for a single framework stack. */
export function dockerfileContentForStack(
  stack: FrameworkOption,
  appName: string,
  port: number,
): string {
  const name = sanitizeAppName(appName)
  const footer = `USER 10001:10001\nEXPOSE ${port}\nHEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\\n  CMD wget -qO- http://127.0.0.1:${port}/health || exit 1\n`

  if (stack === 'fastapi') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage FastAPI image (non-root USER 10001).\nFROM python:3.12-alpine AS builder\nWORKDIR /src\nRUN apk add --no-cache build-base libffi-dev\nCOPY requirements.txt pyproject.toml poetry.lock* ./\nRUN python -m venv /opt/venv\nENV PATH="/opt/venv/bin:$PATH"\nRUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; else pip install --no-cache-dir uvicorn fastapi pydantic; fi\n\nFROM python:3.12-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=builder /opt/venv /opt/venv\nCOPY --chown=10001:10001 . .\nENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=${port}\n${footer}CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${port}"]\n`
  }
  if (stack === 'react_vite') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage React (Vite) image with non-root Nginx.\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; elif [ -f package-lock.json ]; then npm ci; else npm install; fi\nCOPY . .\nRUN npm run build || pnpm run build || yarn build\n\nFROM nginxinc/nginx-unprivileged:alpine AS runtime\nCOPY --from=build --chown=101:101 /src/dist /usr/share/nginx/html\nEXPOSE 8080\nHEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\\n  CMD wget -qO- http://127.0.0.1:8080/ || exit 1\nCMD ["nginx", "-g", "daemon off;"]\n`
  }
  if (stack === 'vuejs') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Vue SPA image (Caddy static server).\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; elif [ -f package-lock.json ]; then npm ci; else npm install; fi\nCOPY . .\nRUN npm run build || pnpm run build || yarn build\n\nFROM caddy:2.8-alpine AS runtime\nCOPY --from=build /src/dist /usr/share/caddy\nEXPOSE 80\nHEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\\n  CMD wget -qO- http://127.0.0.1:80/ || exit 1\nCMD ["caddy", "file-server", "--root", "/usr/share/caddy", "--listen", ":80"]\n`
  }
  if (stack === 'svelte') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Svelte SPA image (BusyBox httpd).\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; elif [ -f package-lock.json ]; then npm ci; else npm install; fi\nCOPY . .\nRUN npm run build || pnpm run build || yarn build\n\nFROM busybox:1.36-uclibc AS runtime\nCOPY --from=build /src/dist /www\nEXPOSE 8080\nHEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\\n  CMD wget -qO- http://127.0.0.1:8080/ || exit 1\nCMD ["httpd", "-f", "-p", "8080", "-h", "/www"]\n`
  }
  if (stack === 'nuxtjs') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Nuxt SSR image (Node runtime, USER 10001).\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; elif [ -f package-lock.json ]; then npm ci; else npm install; fi\nCOPY . .\nRUN npm run build || pnpm run build || yarn build\n\nFROM node:22-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=build --chown=10001:10001 /src/.output ./.output\nENV NODE_ENV=production HOST=0.0.0.0 PORT=${port} NITRO_PORT=${port} NITRO_HOST=0.0.0.0\n${footer}CMD ["node", ".output/server/index.mjs"]\n`
  }
  if (stack === 'nextjs') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Next.js image (standalone mode, USER 10001).\nFROM node:22-alpine AS deps\nWORKDIR /src\nRUN apk add --no-cache libc6-compat\nCOPY package.json package-lock.json* pnpm-lock.yaml* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; else npm ci; fi\n\nFROM node:22-alpine AS builder\nWORKDIR /src\nCOPY --from=deps /src/node_modules ./node_modules\nCOPY . .\nENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production\nRUN npm run build\n\nFROM node:22-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S -G nextjs nextjs && apk add --no-cache ca-certificates wget\nCOPY --from=builder /src/public ./public\nCOPY --from=builder --chown=10001:10001 /src/.next/standalone ./\nCOPY --from=builder --chown=10001:10001 /src/.next/static ./.next/static\nENV NODE_ENV=production PORT=${port}\n${footer}CMD ["node", "server.js"]\n`
  }
  if (stack === 'nestjs' || stack === 'express') {
    const cmd = stack === 'nestjs' ? '["node", "dist/main.js"]' : '["node", "server.js"]'
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage NestJS/Express image (non-root USER 10001).\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY package.json package-lock.json* pnpm-lock.yaml* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; else npm ci; fi\nCOPY . .\nRUN npm run build\n\nFROM node:22-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=build --chown=10001:10001 /src/dist ./dist\nCOPY --from=build --chown=10001:10001 /src/node_modules ./node_modules\nCOPY --from=build --chown=10001:10001 /src/package.json ./package.json\nENV NODE_ENV=production PORT=${port}\n${footer}CMD ${cmd}\n`
  }
  if (stack === 'springboot' || stack === 'java') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Spring Boot image (non-root USER 10001).\nFROM eclipse-temurin:21-jdk-alpine AS build\nWORKDIR /src\nCOPY . .\nRUN if [ -f mvnw ]; then chmod +x mvnw && ./mvnw -q -DskipTests clean package; elif [ -f gradlew ]; then chmod +x gradlew && ./gradlew -q bootJar; else mvn -q -DskipTests package; fi\n\nFROM eclipse-temurin:21-jre-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=build --chown=10001:10001 /src/target/*.jar /app/app.jar\nENV PORT=${port}\n${footer}CMD ["java", "-jar", "/app/app.jar"]\n`
  }
  if (stack === 'python' || stack === 'flask' || stack === 'django') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Python image (non-root USER 10001).\nFROM python:3.12-alpine AS builder\nWORKDIR /src\nRUN apk add --no-cache build-base libffi-dev\nCOPY requirements.txt pyproject.toml poetry.lock* ./\nRUN python -m venv /opt/venv\nENV PATH="/opt/venv/bin:$PATH"\nRUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; else pip install --no-cache-dir uvicorn fastapi; fi\n\nFROM python:3.12-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=builder /opt/venv /opt/venv\nCOPY --chown=10001:10001 . .\nENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=${port}\n${footer}CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${port}"]\n`
  }
  if (stack === 'go') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Go image (non-root USER 10001).\nFROM golang:1.23-alpine AS build\nWORKDIR /src\nRUN apk add --no-cache git ca-certificates\nCOPY go.mod go.sum* ./\nRUN go mod download\nCOPY . .\nRUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/${name} .\n\nFROM alpine:3.21 AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=build --chown=10001:10001 /out/${name} /app/${name}\nENV PORT=${port}\n${footer}CMD ["/app/${name}"]\n`
  }
  if (stack === 'rust') {
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Rust image (non-root USER 10001).\nFROM rust:1.83-alpine AS build\nWORKDIR /src\nRUN apk add --no-cache musl-dev\nCOPY Cargo.toml Cargo.lock* ./\nRUN mkdir src && echo "fn main() {}" > src/main.rs && cargo build --release && rm -rf src\nCOPY . .\nRUN cargo build --release\n\nFROM alpine:3.21 AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=build --chown=10001:10001 /src/target/release/${name} /app/${name}\nENV PORT=${port}\n${footer}CMD ["/app/${name}"]\n`
  }
  return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage Node.js image (non-root USER 10001).\nFROM node:22-alpine AS deps\nWORKDIR /src\nRUN apk add --no-cache libc6-compat\nCOPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; elif [ -f yarn.lock ]; then yarn install --frozen-lockfile; elif [ -f package-lock.json ]; then npm ci; else npm install; fi\n\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY --from=deps /src/node_modules ./node_modules\nCOPY . .\nENV NODE_ENV=production\nRUN if [ -f package.json ] && grep -q '"build"' package.json; then npm run build || yarn build || pnpm build; fi\n\nFROM node:22-alpine AS runtime\nWORKDIR /app\nRUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache ca-certificates wget\nCOPY --from=build --chown=10001:10001 /src ./\nENV NODE_ENV=production PORT=${port}\n${footer}CMD ["node", "server.js"]\n`
}

function dockerComposeForServices(
  services: Array<{ name: string; listenPort: number; dockerfilePath: string }>,
): string {
  const lines: string[] = ['services:']
  for (const service of services) {
    const safeName = sanitizeAppName(service.name)
    const imageEnv = `APP_IMAGE_${safeName.toUpperCase().replace(/[-.]/g, '_')}`
    lines.push(
      `  ${safeName}:`,
      '    build:',
      '      context: .',
      `      dockerfile: ${service.dockerfilePath}`,
      `    image: \${${imageEnv}:-${safeName}:latest}`,
      '    ports:',
      // Preferred host port; API compose deploy remaps to the next free port if busy.
      `      - "${service.listenPort}:${service.listenPort}"`,
      '    environment:',
      `      - PORT=${service.listenPort}`,
      '      - NODE_ENV=production',
      '    restart: unless-stopped',
      '    healthcheck:',
      `      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:${service.listenPort}/health"]`,
      '      interval: 30s',
      '      timeout: 5s',
      '      retries: 3',
      '      start_period: 20s',
      '',
    )
  }
  return `${lines.join('\n').trimEnd()}\n`
}

/**
 * Scaffold meaningful `dockers/Dockerfile.*` paths (+ root docker-compose.yml).
 */
export function buildDockerScaffold(
  cfg: ContainerScaffoldConfig,
  datastores?: Array<{ kind: string }>,
): ScaffoldTarget[] {
  if (!cfg.enabled) return []
  // Multi-service CoreScaffold apps are owned by the API (apps/<slug>/ + compose
  // context). Client-only Dockerfiles assume repo-root context and must not
  // overwrite those artifacts after provision.
  if ((cfg.services?.length ?? 0) > 0) return []

  const stacks = resolveDockerStacks(cfg)
  const multi = stacks.length > 1 || (cfg.frameworks?.length ?? 0) > 0
  const appName = cfg.app_name || 'app'
  const fallbackPort = cfg.listen_port || 8080
  const targets: ScaffoldTarget[] = []
  const composeServices: Array<{ name: string; listenPort: number; dockerfilePath: string }> = []

  for (const stack of stacks) {
    const port = multi
      ? defaultListenPortForStack(stack, fallbackPort)
      : (cfg.listen_port || defaultListenPortForStack(stack, 8080))
    const serviceName = multi ? `${appName}-${stack}` : appName
    const dockerfilePath = dockerfilePathForService(appName, stack, multi)

    if (cfg.generate_dockerfile !== false) {
      targets.push({
        path: dockerfilePath,
        content: dockerfileContentForStack(stack, serviceName, port),
      })
    }
    composeServices.push({
      name: serviceName,
      listenPort: port,
      dockerfilePath,
    })
  }

  if (cfg.generate_docker_compose !== false) {
    targets.push({
      path: 'docker-compose.yml',
      content: dockerComposeForServices(composeServices),
    })
  }
  return targets
}

/**
 * Scaffold GitHub/GitLab CI files.
 * When multiple frameworks are selected, emit one pipeline per framework that
 * builds `dockers/<framework>/Dockerfile`.
 */
export function buildCiCdScaffold(
  platform: CicdPlatform,
  security: CicdSecurityConfig = defaultCicdSecurityConfig(platform),
  frameworks: FrameworkOption[] = [],
  appName = 'app',
): ScaffoldTarget[] {
  const stacks = frameworks.length > 0 ? frameworks : (['app'] as const)
  const multi = frameworks.length > 0
  const resolvedAppName = sanitizeAppName(appName)

  if (platform === 'github') {
    return stacks.map((stack) => {
      const slug = stack === 'app' ? 'deploy' : `${sanitizeServiceSlug(stack)}-deploy`
      const service = stack === 'app' ? resolvedAppName : `${resolvedAppName}-${sanitizeServiceSlug(stack)}`
      const dockerfilePath = stack === 'app'
        ? dockerfilePathForService(resolvedAppName)
        : dockerfilePathForService(resolvedAppName, stack, true)
      return {
        path: `ci/github/workflows/${slug}.yml`,
        content: renderCicdWorkflow(platform, security, {
          deploymentName: service,
          containerName: service,
          dockerfilePath,
          imageRef: multi
            ? `ghcr.io/\${{ github.repository }}/${service}:\${{ github.sha }}`
            : 'ghcr.io/${{ github.repository }}:${{ github.sha }}',
        }),
      }
    })
  }

  // GitLab: one include-root plus one job file per framework (or a single root pipeline).
  // Also write root `.gitlab-ci.yml` so GitLab picks it up without extra config.
  if (!multi) {
    const content = renderCicdWorkflow(platform, security, {
      dockerfilePath: dockerfilePathForService(resolvedAppName),
    })
    return [
      { path: 'ci/gitlab/.gitlab-ci.yml', content },
      { path: '.gitlab-ci.yml', content },
    ]
  }

  const includes = frameworks.map((stack) => ({
    path: `ci/gitlab/${stack}.yml`,
    content: renderCicdWorkflow(platform, security, {
      deploymentName: `${resolvedAppName}-${sanitizeServiceSlug(stack)}`,
      containerName: `${resolvedAppName}-${sanitizeServiceSlug(stack)}`,
      dockerfilePath: dockerfilePathForService(resolvedAppName, stack, true),
      imageRef: `\${CI_REGISTRY_IMAGE}/${resolvedAppName}-${sanitizeServiceSlug(stack)}:\${CI_COMMIT_SHA}`,
    }),
  }))

  const includeRootContent = [
    '# Generated by Launchpad - includes one pipeline fragment per framework',
    'include:',
    ...frameworks.map((stack) => `  - local: ci/gitlab/${stack}.yml`),
    '',
  ].join('\n')

  return [
    { path: 'ci/gitlab/.gitlab-ci.yml', content: includeRootContent },
    { path: '.gitlab-ci.yml', content: includeRootContent },
    ...includes,
  ]
}

/** Detect which CI platform is present in a workspace file tree. */
export function detectCicdPlatformFromPaths(paths: string[]): CicdPlatform | null {
  const hasGithub = paths.some(
    (p) => p.startsWith('ci/github/') || p.startsWith('.github/workflows/'),
  )
  const hasGitlab = paths.some(
    (p) =>
      p.startsWith('ci/gitlab/')
      || p === '.gitlab-ci.yml'
      || p.endsWith('/.gitlab-ci.yml'),
  )
  if (hasGithub && !hasGitlab) return 'github'
  if (hasGitlab && !hasGithub) return 'gitlab'
  if (hasGithub) return 'github'
  if (hasGitlab) return 'gitlab'
  return null
}

/** Paths belonging to a CI platform (for cleanup when switching). */
export function cicdFilePathsForPlatform(platform: CicdPlatform, paths: string[]): string[] {
  if (platform === 'github') {
    return paths.filter(
      (p) => p.startsWith('ci/github/') || p.startsWith('.github/workflows/'),
    )
  }
  return paths.filter(
    (p) =>
      p.startsWith('ci/gitlab/')
      || p === '.gitlab-ci.yml'
      || p.endsWith('/.gitlab-ci.yml'),
  )
}

export function oppositeCicdFilePaths(platform: CicdPlatform, paths: string[]): string[] {
  return cicdFilePathsForPlatform(platform === 'github' ? 'gitlab' : 'github', paths)
}

export type DetectedWorkspaceInfra = {
  provision: { enabled: boolean; engine: ProvisionEngine }
  /** True when ``infra/ansible`` (or ansible.cfg) is present. */
  ansible: { enabled: boolean }
  kubernetes: { enabled: boolean; mode: K8sScaffoldMode }
  cicd: {
    enabled: boolean
    platform: CicdPlatform
    frameworks: FrameworkOption[]
    samplePath: string | null
  }
  container: {
    enabled: boolean
    generate_dockerfile: boolean
    generate_docker_compose: boolean
    frameworks: FrameworkOption[]
  }
  summary: string[]
}

/** Map path slug (`nuxtjs`, `react-vite`) back to a FrameworkOption. */
export function matchFrameworkSlug(raw: string): FrameworkOption | null {
  const cleaned = raw.trim().toLowerCase()
  if (!cleaned) return null
  const underscored = cleaned.replace(/-/g, '_')
  const dashed = cleaned.replace(/_/g, '-')
  for (const id of FRAMEWORK_IDS_BY_LENGTH) {
    if (id === cleaned || id === underscored || sanitizeServiceSlug(id) === dashed) {
      return id
    }
  }
  return null
}

function uniqueFrameworks(items: FrameworkOption[]): FrameworkOption[] {
  return [...new Set(items)]
}

function frameworksFromPaths(paths: string[]): FrameworkOption[] {
  const found: FrameworkOption[] = []

  for (const path of paths) {
    const dockerMatch = path.match(/^dockers\/Dockerfile\.[^/]+-(.+)$/i)
    if (dockerMatch?.[1]) {
      const fw = matchFrameworkSlug(dockerMatch[1])
      if (fw) found.push(fw)
      continue
    }

    const githubMatch = path.match(/^ci\/github\/workflows\/(.+)-deploy\.ya?ml$/i)
    if (githubMatch?.[1] && githubMatch[1].toLowerCase() !== 'deploy') {
      const fw = matchFrameworkSlug(githubMatch[1])
      if (fw) found.push(fw)
      continue
    }

    const gitlabMatch = path.match(/^ci\/gitlab\/([^/]+)\.ya?ml$/i)
    if (gitlabMatch?.[1] && gitlabMatch[1].toLowerCase() !== '.gitlab-ci') {
      const name = gitlabMatch[1]
      if (name !== '.gitlab-ci') {
        const fw = matchFrameworkSlug(name)
        if (fw) found.push(fw)
      }
    }
  }

  return uniqueFrameworks(found)
}

/**
 * Infer provision / kubernetes / CI / docker toggles from workspace file paths
 * so the interactive update form mirrors what is already on disk.
 */
export function detectWorkspaceInfraFromPaths(paths: string[]): DetectedWorkspaceInfra {
  const filePaths = paths.filter(Boolean)
  const summary: string[] = []

  const hasPulumi = filePaths.some(
    (p) => p.startsWith('infra/pulumi/') || /(^|\/)Pulumi(\.[^/]+)?\.ya?ml$/i.test(p),
  )
  const hasTerraform = filePaths.some(
    (p) =>
      p.startsWith('infra/terraform/')
      || /\.tf$/i.test(p)
      || p.endsWith('opentofu.tf')
      || p.includes('/.terraform/'),
  )
  const hasAnsible = filePaths.some(
    (p) =>
      p.startsWith('infra/ansible/')
      || /(^|\/)ansible\.cfg$/i.test(p)
      || p.startsWith('ansible/playbooks/')
      || p.startsWith('ansible/inventory/'),
  )
  const hasLaunchpad = filePaths.some(
    (p) =>
      p === 'infra/launchProvision.sh'
      || p.endsWith('/launchProvision.sh')
      || p.includes('provision/launchpad.sh'),
  )
  const provisionEnabled = hasPulumi || hasTerraform || hasAnsible || hasLaunchpad
  let provisionEngine: ProvisionEngine = 'terraform'
  if (hasLaunchpad && !hasPulumi && !hasTerraform && !hasAnsible) {
    provisionEngine = 'launchpad'
  } else if (hasAnsible && !hasPulumi && !hasTerraform) {
    provisionEngine = 'ansible'
  } else if (hasPulumi && !hasTerraform) {
    provisionEngine = 'pulumi'
  }
  if (hasLaunchpad && !hasPulumi && !hasTerraform) {
    summary.push('LaunchProvision (infra/launchProvision.sh)')
  }
  if (hasPulumi || hasTerraform) {
    const tfOrPu: ProvisionEngine = hasPulumi && !hasTerraform ? 'pulumi' : 'terraform'
    summary.push(`Provision (${tfOrPu})`)
  }
  if (hasAnsible) {
    summary.push('Ansible (infra/ansible)')
  }

  const hasHelm = filePaths.some(
    (p) => p.startsWith('infra/helm/') || /(^|\/)Chart\.ya?ml$/i.test(p),
  )
  const hasKustomize = filePaths.some(
    (p) =>
      p.startsWith('infra/kustomize/')
      || /(^|\/)kustomization\.ya?ml$/i.test(p),
  )
  const hasRawK8s = filePaths.some(
    (p) =>
      p.startsWith('infra/k8s/')
      || p.startsWith('infra/manifests/')
      || p.startsWith('k8s/')
      || p.startsWith('manifests/'),
  )
  const kubernetesEnabled = hasHelm || hasKustomize || hasRawK8s
  const kubernetesMode: K8sScaffoldMode = hasHelm
    ? 'helm'
    : hasKustomize
      ? 'kustomize'
      : 'k8s'
  if (kubernetesEnabled) {
    summary.push(`Kubernetes (${kubernetesMode})`)
  }

  const cicdPlatform = detectCicdPlatformFromPaths(filePaths)
  const cicdEnabled = cicdPlatform !== null
  const cicdFrameworks = cicdEnabled ? frameworksFromPaths(filePaths) : []
  const cicdSample =
    cicdPlatform === 'github'
      ? filePaths.find(
          (p) => p.startsWith('ci/github/') || p.startsWith('.github/workflows/'),
        ) ?? null
      : cicdPlatform === 'gitlab'
        ? filePaths.find(
            (p) =>
              p.startsWith('ci/gitlab/')
              || p === '.gitlab-ci.yml'
              || p.endsWith('/.gitlab-ci.yml'),
          ) ?? null
        : null
  if (cicdEnabled && cicdPlatform) {
    const stacks =
      cicdFrameworks.length > 0 ? ` · ${cicdFrameworks.join(', ')}` : ''
    summary.push(`CI/CD (${cicdPlatform}${stacks})`)
  }

  const hasDockerfile = filePaths.some(
    (p) =>
      /(^|\/)Dockerfile(\.[^/]+)?$/i.test(p)
      || p.startsWith('dockers/'),
  )
  const hasCompose = filePaths.some(
    (p) =>
      /(^|\/)docker-compose(\.[^/]+)?\.ya?ml$/i.test(p)
      || /(^|\/)compose(\.[^/]+)?\.ya?ml$/i.test(p),
  )
  const containerFrameworks = frameworksFromPaths(filePaths)
  const containerEnabled = hasDockerfile || hasCompose
  if (containerEnabled) {
    const stacks =
      containerFrameworks.length > 0 ? ` · ${containerFrameworks.join(', ')}` : ''
    summary.push(`Docker${stacks}`)
  }

  return {
    provision: { enabled: provisionEnabled, engine: provisionEngine },
    ansible: { enabled: hasAnsible },
    kubernetes: { enabled: kubernetesEnabled, mode: kubernetesMode },
    cicd: {
      enabled: cicdEnabled,
      platform: cicdPlatform ?? 'github',
      frameworks: cicdFrameworks,
      samplePath: cicdSample,
    },
    container: {
      enabled: containerEnabled,
      generate_dockerfile: hasDockerfile,
      generate_docker_compose: hasCompose,
      frameworks: containerFrameworks,
    },
    summary,
  }
}

/** Overlay disk detection onto wizard-derived infra + container scaffold. */
export function applyDetectedWorkspaceInfra(
  base: InfraGenerationConfig,
  container: ContainerScaffoldConfig,
  detected: DetectedWorkspaceInfra,
  security?: CicdSecurityConfig,
): { infra: InfraGenerationConfig; container: ContainerScaffoldConfig } {
  const frameworks =
    detected.cicd.frameworks.length > 0
      ? detected.cicd.frameworks
      : detected.container.frameworks.length > 0
        ? detected.container.frameworks
        : container.frameworks.length > 0
          ? container.frameworks
          : []

  const infra: InfraGenerationConfig = {
    provision: {
      enabled: base.provision.enabled || detected.provision.enabled,
      engine: detected.provision.enabled ? detected.provision.engine : base.provision.engine,
    },
    kubernetes: {
      enabled: base.kubernetes.enabled || detected.kubernetes.enabled,
      mode: detected.kubernetes.enabled ? detected.kubernetes.mode : base.kubernetes.mode,
    },
    cicd: {
      enabled: base.cicd.enabled || detected.cicd.enabled,
      platform: detected.cicd.enabled ? detected.cicd.platform : base.cicd.platform,
      security:
        security
        ?? (detected.cicd.enabled
          ? defaultCicdSecurityConfig(detected.cicd.platform)
          : base.cicd.security),
      frameworks: detected.cicd.enabled
        ? (detected.cicd.frameworks.length > 0 ? detected.cicd.frameworks : frameworks)
        : base.cicd.frameworks,
    },
  }

  const nextContainer: ContainerScaffoldConfig = {
    ...container,
    enabled: container.enabled || detected.container.enabled,
    generate_dockerfile:
      container.generate_dockerfile || detected.container.generate_dockerfile,
    generate_docker_compose:
      container.generate_docker_compose || detected.container.generate_docker_compose,
    frameworks:
      frameworks.length > 0 ? frameworks : container.frameworks,
    stack:
      frameworks[0]
      ?? container.stack
      ?? 'generic',
  }

  return { infra, container: nextContainer }
}

export function provisionRunCommands(engine: ProvisionEngine): string[] {
  return iacRunShortcuts(engine)
    .filter((item) => !item.danger)
    .slice(0, 2)
    .map((item) => item.command)
}

export type IacRunShortcut = {
  id: string
  label: string
  command: string
  description?: string
  danger?: boolean
  /** Opens the stepped provision/destroy wizard instead of running immediately. */
  opensInitWizard?: boolean
  /**
   * How the wizard executes this step.
   * - terminal: run `command` in the sandbox (default)
   * - enable_gcp_apis: call control-plane Service Usage enablement
   */
  action?: 'terminal' | 'enable_gcp_apis'
}

/**
 * Shell prefix that cds into the IaC dir only when needed.
 * Works from workspace root or when already inside infra/terraform|pulumi.
 */
export function iacEnsureDirPrefix(engine: ProvisionEngine): string {
  if (engine === 'launchpad') {
    return [
      'if [ -f infra/launchProvision.sh ] || [ -f provision/launchpad.sh ]; then :;',
      'else echo "LaunchProvision script not found (expected infra/launchProvision.sh)" >&2; exit 1; fi;',
    ].join(' ')
  }
  if (engine === 'pulumi') {
    return [
      'if [ -f Pulumi.yaml ] || [ -f Pulumi.yml ]; then :;',
      'elif [ -d infra/pulumi ]; then cd infra/pulumi;',
      'else echo "Pulumi directory not found (expected infra/pulumi or Pulumi.yaml)" >&2; exit 1; fi;',
    ].join(' ')
  }
  if (engine === 'ansible') {
    return [
      'if [ -f ansible.cfg ] && [ -d playbooks ]; then :;',
      'elif [ -d infra/ansible ]; then cd infra/ansible;',
      'else echo "Ansible directory not found (expected infra/ansible)" >&2; exit 1; fi;',
    ].join(' ')
  }
  // terraform + opentofu share infra/terraform
  return [
    'if ls ./*.tf >/dev/null 2>&1 || [ -d .terraform ]; then :;',
    'elif [ -d infra/terraform ]; then cd infra/terraform;',
    'else echo "Terraform directory not found (expected infra/terraform or *.tf here)" >&2; exit 1; fi;',
  ].join(' ')
}

function launchProvisionCmd(action: string): string {
  return [
    'if [ -f infra/launchProvision.sh ]; then bash infra/launchProvision.sh',
    `${action};`,
    'elif [ -f provision/launchpad.sh ]; then bash provision/launchpad.sh',
    `${action};`,
    'else echo "LaunchProvision script not found (expected infra/launchProvision.sh)" >&2; exit 1; fi',
  ].join(' ')
}

function iacCmd(engine: ProvisionEngine, binaryCmd: string): string {
  return `${iacEnsureDirPrefix(engine)} ${binaryCmd}`
}

/** Primary toolbar actions: provision wizard + destroy wizard. */
export function iacToolbarActions(engine: ProvisionEngine): {
  provision: IacRunShortcut
  destroy: IacRunShortcut
} {
  if (engine === 'ansible') {
    return {
      provision: {
        id: 'ansible-provision',
        label: 'Run Ansible',
        command: '',
        description: 'Install collections, dry-run, then apply playbooks/site.yml for the selected cloud host',
        opensInitWizard: true,
      },
      destroy: {
        id: 'ansible-destroy',
        label: 'Destroy',
        command: iacDestroyCommand(engine),
        description: 'Ansible does not tear down cloud infra; use Terraform/Pulumi destroy for that',
        danger: true,
        opensInitWizard: true,
      },
    }
  }
  return {
    provision: {
      id: `${engine}-provision`,
      label: 'Provision stack',
      command: '',
      description: `Run ${iacEngineLabel(engine)} init → validate → plan → apply in the sandbox`,
      opensInitWizard: true,
    },
    destroy: {
      id: `${engine}-destroy`,
      label: 'Destroy',
      command: iacDestroyCommand(engine),
      description: `Tear down ${iacEngineLabel(engine)}-managed cloud resources`,
      danger: true,
      opensInitWizard: true,
    },
  }
}

/** Auto-approved destroy (UI confirms first; no interactive yes prompt in the terminal). */
export function iacDestroyCommand(engine: ProvisionEngine): string {
  if (engine === 'launchpad') {
    return launchProvisionCmd('down')
  }
  if (engine === 'pulumi') {
    return iacCmd(engine, 'pulumi destroy --yes')
  }
  if (engine === 'ansible') {
    return iacCmd(
      engine,
      "echo 'Ansible does not destroy cloud resources; remove app containers/services manually on the host.' >&2; exit 1",
    )
  }
  const bin = engine === 'opentofu' ? 'tofu' : 'terraform'
  return iacCmd(engine, `${bin} destroy -auto-approve`)
}

/** Terminal shortcuts for Terraform / OpenTofu / Pulumi on the Advanced IDE menus. */
export function iacRunShortcuts(engine: ProvisionEngine): IacRunShortcut[] {
  if (engine === 'launchpad') {
    return [
      {
        id: 'lp-up',
        label: 'up',
        command: launchProvisionCmd('up'),
        opensInitWizard: true,
      },
      { id: 'lp-outputs', label: 'outputs', command: launchProvisionCmd('outputs') },
      { id: 'lp-configure', label: 'configure', command: launchProvisionCmd('configure') },
      {
        id: 'lp-down',
        label: 'down',
        command: iacDestroyCommand(engine),
        danger: true,
      },
    ]
  }
  if (engine === 'terraform') {
    return [
      {
        id: 'tf-init',
        label: 'init',
        command: iacCmd(engine, 'terraform init'),
        opensInitWizard: true,
      },
      { id: 'tf-validate', label: 'validate', command: iacCmd(engine, 'terraform validate') },
      { id: 'tf-plan', label: 'plan', command: iacCmd(engine, 'terraform plan') },
      {
        id: 'tf-apply',
        label: 'apply',
        command: iacCmd(engine, 'terraform apply -auto-approve'),
        opensInitWizard: true,
      },
      {
        id: 'tf-destroy',
        label: 'destroy',
        command: iacDestroyCommand(engine),
        danger: true,
      },
    ]
  }
  if (engine === 'opentofu') {
    return [
      {
        id: 'tofu-init',
        label: 'init',
        command: iacCmd(engine, 'tofu init'),
        opensInitWizard: true,
      },
      { id: 'tofu-validate', label: 'validate', command: iacCmd(engine, 'tofu validate') },
      { id: 'tofu-plan', label: 'plan', command: iacCmd(engine, 'tofu plan') },
      {
        id: 'tofu-apply',
        label: 'apply',
        command: iacCmd(engine, 'tofu apply -auto-approve'),
        opensInitWizard: true,
      },
      {
        id: 'tofu-destroy',
        label: 'destroy',
        command: iacDestroyCommand(engine),
        danger: true,
      },
    ]
  }
  if (engine === 'ansible') {
    return [
      {
        id: 'ansible-galaxy',
        label: 'galaxy',
        command: iacCmd(engine, 'ansible-galaxy collection install -r requirements.yml'),
      },
      {
        id: 'ansible-check',
        label: 'check',
        command: iacCmd(engine, 'ansible-playbook playbooks/site.yml --check'),
      },
      {
        id: 'ansible-apply',
        label: 'apply',
        command: iacCmd(engine, 'ansible-playbook playbooks/site.yml'),
        opensInitWizard: true,
      },
    ]
  }
  return [
    { id: 'pu-install', label: 'npm install', command: iacCmd(engine, 'npm install') },
    {
      id: 'pu-preview',
      label: 'preview',
      command: iacCmd(engine, 'pulumi preview'),
    },
    {
      id: 'pu-up',
      label: 'up',
      command: iacCmd(engine, 'pulumi up --yes'),
      opensInitWizard: true,
    },
    { id: 'pu-refresh', label: 'refresh', command: iacCmd(engine, 'pulumi refresh') },
    {
      id: 'pu-down',
      label: 'down',
      command: iacDestroyCommand(engine),
      danger: true,
    },
  ]
}

/** Ordered guided steps for the provision wizard (after credentials). */
export function iacInitWizardSteps(
  engine: ProvisionEngine,
  opts: { enableGcpApis?: boolean } = {},
): IacRunShortcut[] {
  const enableApisStep: IacRunShortcut | null = opts.enableGcpApis
    ? {
        id: 'gcp-enable-apis',
        label: 'enable APIs',
        description:
          'Enable required Google APIs (Compute, GKE, …) on the project before Terraform runs',
        command: '',
        action: 'enable_gcp_apis',
      }
    : null

  if (engine === 'launchpad') {
    const launchpadSteps: IacRunShortcut[] = [
      {
        id: 'lp-up',
        label: 'up',
        description: 'Create cloud resources with infra/launchProvision.sh',
        command: launchProvisionCmd('up'),
      },
      {
        id: 'lp-configure',
        label: 'configure',
        description: 'Apply LaunchConfig on the instance (first-boot / startup metadata)',
        command: launchProvisionCmd('configure'),
      },
    ]
    return enableApisStep ? [enableApisStep, ...launchpadSteps] : launchpadSteps
  }
  if (engine === 'pulumi') {
    const pulumiSteps: IacRunShortcut[] = [
      {
        id: 'pu-install',
        label: 'npm install',
        description: 'Install Pulumi program dependencies',
        command: iacCmd(engine, 'npm install'),
      },
      {
        id: 'pu-preview',
        label: 'preview',
        description: 'Show the planned resource changes without applying',
        command: iacCmd(engine, 'pulumi preview'),
      },
      {
        id: 'pu-up',
        label: 'up',
        description: 'Create or update stack resources (auto-approved)',
        command: iacCmd(engine, 'pulumi up --yes'),
      },
    ]
    return enableApisStep ? [enableApisStep, ...pulumiSteps] : pulumiSteps
  }
  if (engine === 'ansible') {
    const ansibleSteps: IacRunShortcut[] = [
      {
        id: 'ansible-galaxy',
        label: 'galaxy',
        description: 'Install Ansible collections from requirements.yml',
        command: iacCmd(engine, 'ansible-galaxy collection install -r requirements.yml'),
      },
      {
        id: 'ansible-check',
        label: 'check',
        description: 'Dry-run the site playbook against inventory hosts',
        command: iacCmd(engine, 'ansible-playbook playbooks/site.yml --check'),
      },
      {
        id: 'ansible-apply',
        label: 'apply',
        description: 'Configure the VM / Compose host with Ansible',
        command: iacCmd(engine, 'ansible-playbook playbooks/site.yml'),
      },
    ]
    return enableApisStep ? [enableApisStep, ...ansibleSteps] : ansibleSteps
  }
  const bin = engine === 'opentofu' ? 'tofu' : 'terraform'
  const label = engine === 'opentofu' ? 'OpenTofu' : 'Terraform'
  const tfSteps: IacRunShortcut[] = [
    {
      id: `${bin}-init`,
      label: 'init',
      description: `Download providers and initialize the ${label} working directory`,
      command: iacCmd(engine, `${bin} init`),
    },
    {
      id: `${bin}-validate`,
      label: 'validate',
      description: 'Check configuration syntax and provider arguments',
      command: iacCmd(engine, `${bin} validate`),
    },
    {
      id: `${bin}-plan`,
      label: 'plan',
      description: 'Preview create/update/destroy actions before applying',
      command: iacCmd(engine, `${bin} plan`),
    },
    {
      id: `${bin}-apply`,
      label: 'apply',
      description: `Apply the plan to your cloud project (auto-approved - no yes prompt)`,
      command: iacCmd(engine, `${bin} apply -auto-approve`),
    },
  ]
  return enableApisStep ? [enableApisStep, ...tfSteps] : tfSteps
}

/** Single destroy step for the destroy wizard. */
export function iacDestroyWizardSteps(engine: ProvisionEngine): IacRunShortcut[] {
  const label = iacEngineLabel(engine)
  return [
    {
      id: `${engine}-destroy`,
      label: 'destroy',
      description: `Destroy ${label}-managed resources (auto-approved after you confirm)`,
      command: iacDestroyCommand(engine),
      danger: true,
    },
  ]
}

export function iacEngineLabel(engine: ProvisionEngine): string {
  if (engine === 'launchpad') return 'LaunchProvision'
  if (engine === 'opentofu') return 'OpenTofu'
  if (engine === 'pulumi') return 'Pulumi'
  if (engine === 'ansible') return 'Ansible'
  return 'Terraform'
}

export function kubernetesRunCommands(mode: K8sScaffoldMode): string[] {
  if (mode === 'k8s') {
    return ['kubectl apply -f infra/k8s/manifests/']
  }
  if (mode === 'kustomize') {
    return ['kubectl apply -k infra/kustomize/overlays/prod/']
  }
  return ['helm upgrade --install app-chart infra/helm/app-chart/']
}
