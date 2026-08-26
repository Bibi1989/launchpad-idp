import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { InjectQueue } from '@nestjs/bullmq';
import { Queue } from 'bullmq';
import { randomUUID } from 'crypto';

import { DockerfileBuildStore } from '../queues/dockerfile-build.store';

// Mirrors FastAPI ProjectStack (apps/api/app/schemas/dockerfile_schema.py).
type ProjectStack =
  | 'react_vite'
  | 'nextjs'
  | 'nuxtjs'
  | 'vuejs'
  | 'svelte'
  | 'angular'
  | 'fastapi'
  | 'flask'
  | 'django'
  | 'express'
  | 'nestjs'
  | 'springboot'
  | 'dotnet'
  | 'node'
  | 'python'
  | 'go'
  | 'java'
  | 'rust'
  | 'generic'
  | 'unknown';

const PROJECT_STACKS: readonly ProjectStack[] = [
  'react_vite',
  'nextjs',
  'nuxtjs',
  'vuejs',
  'svelte',
  'angular',
  'fastapi',
  'flask',
  'django',
  'express',
  'nestjs',
  'springboot',
  'dotnet',
  'node',
  'python',
  'go',
  'java',
  'rust',
  'generic',
  'unknown',
];

type DockerfileSeverity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

interface DockerfileSecurityIssue {
  ruleId: string;
  severity: DockerfileSeverity;
  description: string;
  lineNumber: number | null;
}

@Injectable()
export class DockerfilesService {
  private readonly logger = new Logger(DockerfilesService.name);

  constructor(
    private readonly buildStore: DockerfileBuildStore,
    @InjectQueue('provisioning') private readonly provisioningQueue: Queue,
    private readonly configService: ConfigService,
  ) {}

  // Mirrors FastAPI DockerfileManagerService.scan -> DockerfileScanResponse.
  // GitHub App scanning is not wired into the NestJS control-plane, so we return
  // the same response shape with an empty dockerfiles list (scaffold suggested).
  async scan(payload: any): Promise<any> {
    const fullName: string = payload?.full_name || '';
    const ref: string = (payload?.ref || 'main').toString();
    return {
      full_name: fullName,
      ref,
      dockerfiles: [],
      detected_stack: this.coerceStack(payload?.stack) ?? 'unknown',
      scaffold_suggested: true,
      root_markers: [],
    };
  }

  // Mirrors FastAPI DockerfileManagerService.scaffold -> DockerfileScaffoldResponse.
  async scaffold(payload: any): Promise<any> {
    const stack: ProjectStack = this.coerceStack(payload?.stack) ?? 'unknown';
    const fullName: string | undefined = payload?.full_name || undefined;
    const appName: string =
      payload?.app_name || (fullName ? fullName.split('/').pop() || 'app' : 'app');
    const listenPort: number = this.coercePort(payload?.listen_port, 8080);

    const content = this.scaffoldDockerfile(stack, appName, listenPort);
    const relPath = this.dockerfilePathForService(appName);
    return {
      stack,
      path: relPath,
      content,
      detected_from: [] as string[],
    };
  }

  // Mirrors FastAPI DockerfileManagerService.review -> DockerfileReviewResponse.
  // Gemini is not available in this environment, so the heuristic branch is used,
  // matching FastAPI's DockerfileSecurityService._heuristic_report shape.
  async review(payload: any): Promise<any> {
    const content: string = (payload?.dockerfile_content || '').trim();
    const sourcePath: string | null = payload?.source_path ?? null;
    if (!content) {
      throw new BadRequestException({
        code: 'dockerfile_review_failed',
        message: 'Dockerfile content is empty',
      });
    }
    const stack: ProjectStack = this.coerceStack(payload?.stack) ?? 'unknown';
    const report = this.heuristicReport(content, stack);
    return {
      report,
      source_path: sourcePath,
    };
  }

  // Mirrors FastAPI DockerfileManagerService.push. Pushing requires a GitHub App
  // installation client, which is not configured in the NestJS control-plane, so
  // we surface FastAPI's structured error branch (HTTP 400 + {code, message}).
  async push(_payload: any): Promise<any> {
    throw new BadRequestException({
      code: 'dockerfile_push_failed',
      message: 'GitHub integration is not configured for this control-plane',
    });
  }

  // Mirrors FastAPI DockerfileManagerService.push_bundle unconfigured branch.
  async pushBundle(_payload: any): Promise<any> {
    throw new BadRequestException({
      code: 'repo_push_bundle_failed',
      message: 'GitHub integration is not configured for this control-plane',
    });
  }

  async enqueueBuild(payload: any): Promise<any> {
    const jobId = `job-${randomUUID()}`;
    this.buildStore.create(jobId, ['Build started']);

    // Hand the build off to the BullMQ provisioning worker (parity with FastAPI's
    // enqueue_dockerfile_build). The worker advances the shared build-job store.
    try {
      await this.provisioningQueue.add('build-dockerfile', {
        action: 'build-dockerfile',
        payload: {
          jobId,
          fullName: payload.full_name || payload.repo || 'app',
          branch: payload.branch || 'main',
          registry: payload.registry || 'localhost:5000',
          tags: payload.tags || [payload.image_tag || 'latest'],
          dockerfilePath: payload.dockerfile_path || 'Dockerfile',
          contextPath: payload.context_path || '.',
        },
      });
    } catch (err) {
      this.logger.error(`Failed to enqueue dockerfile build ${jobId}`, err as Error);
      this.buildStore.markFailed(jobId, 'Failed to enqueue build');
    }

    // Matches FastAPI's DockerfileBuildEnqueueResponse.
    return { job_id: jobId, status: 'queued' };
  }

  async getBuildJob(jobId: string): Promise<any> {
    const job = this.buildStore.get(jobId);
    if (!job) {
      // Unknown job id: report failed rather than fabricating success.
      return {
        job_id: jobId,
        status: 'failed',
        image_refs: [],
        logs: [],
        error: 'Build job not found',
      };
    }
    return job;
  }

  // ---------------------------------------------------------------------------
  // Heuristic helpers ported from apps/api/app/services/dockerfile_scaffold.py
  // and dockerfile_security.py to keep the control-plane responses at parity.
  // ---------------------------------------------------------------------------

  private coerceStack(value: unknown): ProjectStack | null {
    if (typeof value !== 'string') {
      return null;
    }
    const normalized = value.trim().toLowerCase() as ProjectStack;
    return PROJECT_STACKS.includes(normalized) ? normalized : null;
  }

  private coercePort(value: unknown, fallback: number): number {
    const parsed = Number(value);
    if (Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535) {
      return parsed;
    }
    return fallback;
  }

  private sanitizeName(name: string): string {
    const cleaned = (name || '').trim().replace(/[^a-zA-Z0-9._-]+/g, '-') || 'app';
    return cleaned.slice(0, 64);
  }

  private dockerfilePathForService(appName: string): string {
    return `dockers/Dockerfile.${this.sanitizeName(appName)}`;
  }

  private commonFooter(port: number): string {
    return `USER 10001:10001
EXPOSE ${port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:${port}/health || exit 1
`;
  }

  private scaffoldDockerfile(stack: ProjectStack, appName: string, listenPort: number): string {
    const safeName = this.sanitizeName(appName);
    const port = listenPort;
    const generators: Record<ProjectStack, (n: string, p: number) => string> = {
      react_vite: (n, p) => this.reactViteDockerfile(n, p),
      nextjs: (n, p) => this.nextjsDockerfile(n, p),
      nuxtjs: (n, p) => this.nuxtDockerfile(n, p),
      vuejs: (n, p) => this.vueDockerfile(n, p),
      svelte: (n, p) => this.svelteDockerfile(n, p),
      angular: (n, p) => this.angularDockerfile(n, p),
      dotnet: (n, p) => this.dotnetDockerfile(n, p),
      fastapi: (n, p) => this.fastapiDockerfile(n, p),
      flask: (n, p) => this.pythonDockerfile(n, p),
      django: (n, p) => this.pythonDockerfile(n, p),
      express: (n, p) => this.nodeDockerfile(n, p),
      nestjs: (n, p) => this.nestjsDockerfile(n, p),
      springboot: (n, p) => this.springbootDockerfile(n, p),
      node: (n, p) => this.nodeDockerfile(n, p),
      python: (n, p) => this.pythonDockerfile(n, p),
      go: (n, p) => this.goDockerfile(n, p),
      java: (n, p) => this.javaDockerfile(n, p),
      rust: (n, p) => this.rustDockerfile(n, p),
      generic: (n, p) => this.genericDockerfile(n, p),
      unknown: (n, p) => this.genericDockerfile(n, p),
    };
    const gen = generators[stack] || generators.generic;
    return gen(safeName, port);
  }

  private nodeDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Node.js image (non-root USER 10001).

FROM node:22-alpine AS deps
WORKDIR /src
RUN apk add --no-cache libc6-compat
COPY . .
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
${this.commonFooter(port)}CMD ["sh", "-c", "if [ -f server.js ]; then exec node server.js; elif [ -f index.js ]; then exec node index.js; elif [ -f app.js ]; then exec node app.js; elif [ -f dist/server.js ]; then exec node dist/server.js; elif [ -f dist/index.js ]; then exec node dist/index.js; else exec npm start; fi"]
# Image: ${appName}
`;
  }

  private pythonDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Python image (non-root USER 10001).

FROM python:3.12-alpine AS builder
WORKDIR /src
RUN apk add --no-cache build-base libffi-dev
COPY . .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN \\
  if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; \\
  elif [ -f pyproject.toml ] || [ -f setup.py ]; then pip install --no-cache-dir .; \\
  elif [ -f Pipfile ]; then pip install --no-cache-dir pipenv && pipenv install --system --deploy; \\
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
${this.commonFooter(port)}CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${port}"]
# Image: ${appName}
`;
  }

  private goDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Go image (non-root USER 10001).

FROM golang:1.23-alpine AS build
WORKDIR /src
RUN apk add --no-cache git ca-certificates
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/${appName} .

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /out/${appName} /app/${appName}
ENV PORT=${port}
${this.commonFooter(port)}CMD ["/app/${appName}"]
# Image: ${appName}
`;
  }

  private javaDockerfile(appName: string, port: number): string {
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
${this.commonFooter(port)}CMD ["java", "-jar", "/app/app.jar"]
# Image: ${appName}
`;
  }

  private rustDockerfile(appName: string, port: number): string {
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
COPY --from=build --chown=10001:10001 /src/target/release/${appName} /app/${appName}
ENV PORT=${port}
${this.commonFooter(port)}CMD ["/app/${appName}"]
# Image: ${appName}
`;
  }

  private genericDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage generic image (non-root USER 10001).
# Replace the build stage with your language toolchain.

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
${this.commonFooter(port)}CMD ["sleep", "infinity"]
# Image: ${appName}
`;
  }

  private fastapiDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage FastAPI image (non-root USER 10001).

FROM python:3.12-alpine AS builder
WORKDIR /src
RUN apk add --no-cache build-base libffi-dev
COPY . .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN \\
  if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; \\
  elif [ -f pyproject.toml ] || [ -f setup.py ]; then pip install --no-cache-dir .; \\
  elif [ -f Pipfile ]; then pip install --no-cache-dir pipenv && pipenv install --system --deploy; \\
  else pip install --no-cache-dir uvicorn fastapi pydantic; fi

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
${this.commonFooter(port)}CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${port}"]
# Image: ${appName}
`;
  }

  private vueDockerfile(appName: string, _port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Vue SPA image (Caddy static server, non-root).

FROM node:22-alpine AS build
WORKDIR /src
COPY . .
ARG VITE_API_URL=
ARG VUE_APP_API_URL=
ENV VITE_API_URL=$VITE_API_URL
ENV VUE_APP_API_URL=$VUE_APP_API_URL
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM caddy:2.8-alpine AS runtime
COPY --from=build /src/dist /usr/share/caddy
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:80/ || exit 1
CMD ["caddy", "file-server", "--root", "/usr/share/caddy", "--listen", ":80"]
# Image: ${appName}
`;
  }

  private svelteDockerfile(appName: string, _port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Svelte SPA image (BusyBox httpd, non-root).

FROM node:22-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM busybox:1.36-uclibc AS runtime
COPY --from=build /src/dist /www
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/ || exit 1
CMD ["httpd", "-f", "-p", "8080", "-h", "/www"]
# Image: ${appName}
`;
  }

  private angularDockerfile(appName: string, _port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Angular SPA image (non-root Nginx).

FROM node:22-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=build --chown=101:101 /src/dist/*/browser /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/ || exit 1
# Image: ${appName}
`;
  }

  private dotnetDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage .NET image (non-root).

FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY *.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app ./
ENV ASPNETCORE_URLS=http://0.0.0.0:${port} PORT=${port}
USER 1654
EXPOSE ${port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:${port}/health || exit 1
CMD ["dotnet", "app.dll"]
# Image: ${appName}
`;
  }

  private nuxtDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Nuxt SSR image (Node runtime, USER 10001).

FROM node:22-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/.output ./.output
ENV NODE_ENV=production HOST=0.0.0.0 PORT=${port} NITRO_PORT=${port} NITRO_HOST=0.0.0.0
${this.commonFooter(port)}CMD ["node", ".output/server/index.mjs"]
# Image: ${appName}
`;
  }

  private reactViteDockerfile(appName: string, _port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage React (Vite) image with non-root Nginx.

FROM node:22-alpine AS build
WORKDIR /src
COPY . .
# SPA public API base must be present at build time (Vite inlines import.meta.env.*).
# Default to same-origin /api so the browser never sees the string "undefined".
ARG VITE_API_URL=
ARG VITE_API_BASE_URL=
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN \\
  if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; \\
  elif [ -f package-lock.json ]; then npm ci; \\
  else npm install; fi
COPY . .
RUN npm run build || pnpm run build || yarn build

FROM nginxinc/nginx-unprivileged:alpine AS runtime
COPY --from=build --chown=101:101 /src/dist /usr/share/nginx/html
# Default SPA config; Launchpad may replace this at deploy time with an /api proxy.
RUN cat > /etc/nginx/conf.d/default.conf <<'NGINX_EOF'
server {
  listen 8080;
  server_name _;
  root /usr/share/nginx/html;
  index index.html;
  location /health {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_api http://api:8080;
    proxy_pass $target_api;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_api_8000;
  }

  location /api {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_api http://api:8080;
    proxy_pass $target_api;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_api_8000;
  }
  location @fallback_api_8000 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_api_8000 http://api:8000;
    proxy_pass $target_api_8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_api_3000;
  }
  location @fallback_api_3000 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_api_3000 http://api:3000;
    proxy_pass $target_api_3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_backend_8080;
  }
  location @fallback_backend_8080 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_backend_8080 http://backend:8080;
    proxy_pass $target_backend_8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_backend_8000;
  }
  location @fallback_backend_8000 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_backend_8000 http://backend:8000;
    proxy_pass $target_backend_8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_backend_3000;
  }
  location @fallback_backend_3000 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_backend_3000 http://backend:3000;
    proxy_pass $target_backend_3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_server_8080;
  }
  location @fallback_server_8080 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_server_8080 http://server:8080;
    proxy_pass $target_server_8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    error_page 502 504 = @fallback_server_3000;
  }
  location @fallback_server_3000 {
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $target_server_3000 http://server:3000;
    proxy_pass $target_server_3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
NGINX_EOF
RUN chown 101:101 /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
# Image: ${appName}
`;
  }

  private nextjsDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Next.js image (standalone mode, USER 10001).

FROM node:22-alpine AS deps
WORKDIR /src
RUN apk add --no-cache libc6-compat
COPY . .
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; else npm ci; fi

FROM node:22-alpine AS builder
WORKDIR /src
COPY --from=deps /src/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S -G nextjs nextjs \\
  && apk add --no-cache ca-certificates wget
COPY --from=builder /src/public ./public
COPY --from=builder --chown=10001:10001 /src/.next/standalone ./
COPY --from=builder --chown=10001:10001 /src/.next/static ./.next/static
ENV NODE_ENV=production PORT=${port}
${this.commonFooter(port)}CMD ["node", "server.js"]
# Image: ${appName}
`;
  }

  private nestjsDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage NestJS image (non-root USER 10001).

FROM node:22-alpine AS build
WORKDIR /src
COPY . .
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm i --frozen-lockfile; else npm ci; fi
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/dist ./dist
COPY --from=build --chown=10001:10001 /src/node_modules ./node_modules
COPY --from=build --chown=10001:10001 /src/package.json ./package.json
ENV NODE_ENV=production PORT=${port}
${this.commonFooter(port)}CMD ["node", "dist/main.js"]
# Image: ${appName}
`;
  }

  private springbootDockerfile(appName: string, port: number): string {
    return `# syntax=docker/dockerfile:1.7
# Launchpad hardened multi-stage Spring Boot image (non-root USER 10001).

FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /src
COPY . .
RUN \\
  if [ -f mvnw ]; then chmod +x mvnw && ./mvnw -q -DskipTests clean package; \\
  elif [ -f gradlew ]; then chmod +x gradlew && ./gradlew -q bootJar; \\
  else mvn -q -DskipTests package; fi

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /src/target/*.jar /app/app.jar
ENV PORT=${port}
${this.commonFooter(port)}CMD ["java", "-jar", "/app/app.jar"]
# Image: ${appName}
`;
  }

  // Ported from DockerfileSecurityService._heuristic_report (dockerfile_security.py).
  private heuristicReport(content: string, stack: ProjectStack): any {
    const issues: DockerfileSecurityIssue[] = [];
    const lines = content.split('\n');
    let fromCount = 0;

    lines.forEach((line, index) => {
      const idx = index + 1;
      const stripped = line.trim();
      const upper = stripped.toUpperCase();

      if (upper.startsWith('FROM ')) {
        fromCount += 1;
        const image = stripped
          .slice(5)
          .split(' AS ')[0]
          .split(' as ')[0]
          .trim();
        const lastSegment = image.split('/').pop() || image;
        if (image.endsWith(':latest') || !lastSegment.includes(':')) {
          issues.push({
            ruleId: 'UNPINNED_BASE_IMAGE',
            severity: 'HIGH',
            description: `Base image is unpinned or uses :latest (${image})`,
            lineNumber: idx,
          });
        }
        if (image.includes(':latest')) {
          issues.push({
            ruleId: 'LATEST_TAG',
            severity: 'HIGH',
            description: 'Avoid :latest tags for reproducible builds',
            lineNumber: idx,
          });
        }
      }

      if (
        upper.startsWith('USER ') &&
        ['USER ROOT', 'USER 0', 'USER 0:0'].includes(upper)
      ) {
        issues.push({
          ruleId: 'RUN_AS_ROOT',
          severity: 'CRITICAL',
          description: 'Container explicitly runs as root',
          lineNumber: idx,
        });
      }

      if (
        /(api[_-]?key|secret|password|token|private[_-]?key)\s*=\s*\S+/i.test(stripped) &&
        !stripped.startsWith('#')
      ) {
        issues.push({
          ruleId: 'LEAKED_SECRET',
          severity: 'CRITICAL',
          description: 'Possible plain-text secret in Dockerfile instruction',
          lineNumber: idx,
        });
      }
    });

    const hasUser = lines.some((line) => line.trim().toUpperCase().startsWith('USER '));
    if (!hasUser) {
      issues.push({
        ruleId: 'RUN_AS_ROOT',
        severity: 'CRITICAL',
        description: 'No USER directive - image defaults to root',
        lineNumber: null,
      });
    } else if (!lines.some((line) => /^USER\s+10001/i.test(line.trim()))) {
      const hasNonroot = lines
        .filter((line) => line.trim().toUpperCase().startsWith('USER '))
        .some((line) => line.toLowerCase().includes('nonroot'));
      if (!hasNonroot) {
        issues.push({
          ruleId: 'RUN_AS_ROOT',
          severity: 'MEDIUM',
          description: 'Prefer numeric non-root USER 10001 for portability',
          lineNumber: null,
        });
      }
    }

    const hasMultiStage = fromCount >= 2;
    if (!hasMultiStage) {
      issues.push({
        ruleId: 'MISSING_MULTI_STAGE',
        severity: 'MEDIUM',
        description: 'Single-stage Dockerfile increases attack surface',
        lineNumber: null,
      });
    }

    const improved = this.scaffoldDockerfile(stack, 'app', 8080);
    const explanations = [
      'Converted to multi-stage build with minimal runtime image',
      'Enforced non-root USER 10001 (or distroless nonroot)',
      'Pinned alpine/distroless base tags; removed :latest',
      'Removed opportunities for plain-text secrets in image layers',
      'Reordered layers for dependency cache efficiency',
    ];

    const critical = issues.filter((i) => i.severity === 'CRITICAL').length;
    const high = issues.filter((i) => i.severity === 'HIGH').length;
    const summary =
      `Found ${issues.length} issue(s) (${critical} critical, ${high} high). ` +
      'A hardened multi-stage Dockerfile was generated from stack heuristics.';

    return {
      summary,
      securityIssues: issues,
      hasMultiStage,
      improvedDockerfile: improved,
      explanationOfChanges: explanations,
      analysisSource: 'heuristic',
    };
  }
}
