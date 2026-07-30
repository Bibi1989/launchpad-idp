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
import {
  defaultCicdSecurityConfig,
  renderCicdWorkflow,
  type CicdSecurityConfig,
} from '~/utils/cicdWorkflowGenerator'

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
    provision: { enabled: true, engine: 'terraform' },
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
  if (hasKubernetes) return 'manifest_only'
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
          '          image: nginx:1.27-alpine',
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
          '          image: nginx:1.27-alpine',
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
      content: ['replicaCount: 1', 'image:', '  repository: nginx', '  tag: "1.27-alpine"', ''].join('\n'),
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
  if (stack === 'react_vite' || stack === 'vuejs' || stack === 'svelte' || stack === 'nuxtjs') {
    const distDir = stack === 'nuxtjs' ? '.output/public' : 'dist'
    return `# syntax=docker/dockerfile:1.7\n# Launchpad hardened multi-stage static web app image with non-root Nginx.\nFROM node:22-alpine AS build\nWORKDIR /src\nCOPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./\nRUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; elif [ -f package-lock.json ]; then npm ci; else npm install; fi\nCOPY . .\nRUN npm run build || pnpm run build || yarn build\n\nFROM nginxinc/nginx-unprivileged:alpine AS runtime\nCOPY --from=build --chown=101:101 /src/${distDir} /usr/share/nginx/html\nEXPOSE 8080\nHEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\\n  CMD wget -qO- http://127.0.0.1:8080/ || exit 1\nCMD ["nginx", "-g", "daemon off;"]\n`
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
      `    container_name: ${safeName}`,
      '    ports:',
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
export function buildDockerScaffold(cfg: ContainerScaffoldConfig): ScaffoldTarget[] {
  if (!cfg.enabled) return []

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
  if (!multi) {
    return [
      {
        path: 'ci/gitlab/.gitlab-ci.yml',
        content: renderCicdWorkflow(platform, security, {
          dockerfilePath: dockerfilePathForService(resolvedAppName),
        }),
      },
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

  const root = {
    path: 'ci/gitlab/.gitlab-ci.yml',
    content: [
      '# Generated by Launchpad — includes one pipeline fragment per framework',
      'include:',
      ...frameworks.map((stack) => `  - local: ci/gitlab/${stack}.yml`),
      '',
    ].join('\n'),
  }
  return [root, ...includes]
}

export function provisionRunCommands(engine: ProvisionEngine): string[] {
  if (engine === 'terraform') {
    return ['cd infra/terraform && terraform init', 'cd infra/terraform && terraform plan']
  }
  if (engine === 'opentofu') {
    return ['cd infra/terraform && tofu init', 'cd infra/terraform && tofu plan']
  }
  return ['cd infra/pulumi && npm install', 'cd infra/pulumi && pulumi preview']
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
