import {
  ConflictException,
  ForbiddenException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { and, desc, eq, or } from 'drizzle-orm';
import { randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import { catalogServices, CatalogServiceRow } from '../database/schema';

/**
 * Golden path template response shape.
 *
 * Mirrors FastAPI's `GoldenPathTemplateRead` (apps/api/app/schemas/catalog.py)
 * so both control planes return an identical `/catalog/templates` payload.
 */
export interface GoldenPathTemplate {
  id: string;
  version: string;
  title: string;
  description: string;
  icon: string;
  stack: string;
  frameworks: string[];
  docker_images: string[];
  default_tier: string;
  default_slo: string;
  listen_port: number;
  tags: string[];
  includes_dockerfile: boolean;
  includes_k8s: boolean;
  includes_cicd: boolean;
  includes_iac: boolean;
  enable_postgres: boolean;
  enable_redis: boolean;
}

export interface ScorecardItem {
  id: string;
  title: string;
  passed: boolean;
  points: number;
  max_points: number;
  detail: string;
}

export interface ServiceScorecard {
  score: number;
  gate: number;
  passed: boolean;
  items: ScorecardItem[];
}

/**
 * Catalog service response shape.
 *
 * Mirrors FastAPI's `CatalogServiceRead` (snake_case keys).
 */
export interface CatalogServiceRead {
  id: string;
  name: string;
  description: string;
  owner: string;
  tier: string;
  slo_target: string;
  runbook_url: string | null;
  on_call: string | null;
  template_id: string;
  template_version: string;
  repository_url: string | null;
  workspace_id: string | null;
  compliance_score: number;
  scorecard: ServiceScorecard;
  org_id: string | null;
  initial_preview_id: string | null;
  initial_preview_url: string | null;
  created_at: Date;
  updated_at: Date;
}

/**
 * Create payload, mirroring FastAPI's `CatalogServiceCreate`.
 *
 * The NestJS control plane is a simulated control plane (see MEMORY: Nest worker
 * parity), so VCS/preview provisioning fields are accepted for contract parity but
 * do not drive real infrastructure here.
 */
export interface CatalogServiceCreateDto {
  name: string;
  description?: string;
  template_id: string;
  owner: string;
  tier?: string;
  slo_target?: string;
  runbook_url?: string | null;
  on_call?: string | null;
  vcs_provider?: 'none' | 'github' | 'gitlab';
  create_github_repo?: boolean;
  github_installation_id?: number | null;
  github_organization?: string | null;
  github_private?: boolean;
  gitlab_project_name?: string | null;
  gitlab_private?: boolean;
  enforce_scorecard_gate?: boolean;
  trigger_initial_preview?: boolean;
}

export interface CatalogServiceUpdateDto {
  description?: string | null;
  owner?: string | null;
  tier?: string | null;
  slo_target?: string | null;
  runbook_url?: string | null;
  on_call?: string | null;
}

// Runtime / base images used by Launchpad Dockerfile scaffolds (per framework).
// Mirrors apps/api/app/services/golden_path_templates.py::_FRAMEWORK_DOCKER_IMAGES.
const FRAMEWORK_DOCKER_IMAGES: Record<string, string[]> = {
  fastapi: ['python:3.12-alpine'],
  flask: ['python:3.12-alpine'],
  django: ['python:3.12-alpine'],
  python: ['python:3.12-alpine'],
  express: ['node:22-alpine'],
  nestjs: ['node:22-alpine'],
  node: ['node:22-alpine'],
  nextjs: ['node:22-alpine'],
  nuxtjs: ['node:22-alpine'],
  react_vite: ['node:22-alpine', 'nginxinc/nginx-unprivileged:alpine'],
  vuejs: ['node:22-alpine', 'caddy:2.8-alpine'],
  svelte: ['node:22-alpine', 'busybox:1.36-uclibc'],
  go: ['golang:1.23-alpine', 'alpine:3.21'],
  rust: ['rust:1.83-alpine', 'alpine:3.21'],
  springboot: ['eclipse-temurin:21-jdk-alpine', 'eclipse-temurin:21-jre-alpine'],
  java: ['eclipse-temurin:21-jdk-alpine', 'eclipse-temurin:21-jre-alpine'],
  generic: ['alpine:3.21'],
};

function dockerImagesForFrameworks(frameworks: string[]): string[] {
  const images: string[] = [];
  const seen = new Set<string>();
  for (const framework of frameworks) {
    for (const image of FRAMEWORK_DOCKER_IMAGES[framework] ?? []) {
      if (!seen.has(image)) {
        seen.add(image);
        images.push(image);
      }
    }
  }
  return images;
}

// Raw template definitions with the same defaults as the FastAPI dataclass.
interface GoldenPathTemplateDef {
  id: string;
  version: string;
  title: string;
  description: string;
  icon: string;
  stack: string;
  frameworks: string[];
  default_tier: string;
  default_slo: string;
  listen_port: number;
  tags: string[];
  includes_dockerfile?: boolean;
  includes_k8s?: boolean;
  includes_cicd?: boolean;
  includes_iac?: boolean;
  enable_postgres?: boolean;
  enable_redis?: boolean;
}

// Mirrors apps/api/app/services/golden_path_templates.py::GOLDEN_PATH_TEMPLATES.
const GOLDEN_PATH_TEMPLATE_DEFS: GoldenPathTemplateDef[] = [
  // --- Single services ---
  {
    id: 'fastapi-api',
    version: '1.0.0',
    title: 'FastAPI service',
    description:
      'Python FastAPI API with Dockerfile, raw K8s manifests, and GitHub Actions (Trivy + Semgrep).',
    icon: 'api',
    stack: 'fastapi',
    frameworks: ['fastapi'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 8000,
    tags: ['python', 'api', 'backend'],
  },
  {
    id: 'express-api',
    version: '1.0.0',
    title: 'Express API',
    description:
      'Node Express API with hardened multi-stage Dockerfile, K8s Deployment/Service, and CI security scans.',
    icon: 'terminal',
    stack: 'express',
    frameworks: ['express'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 3000,
    tags: ['node', 'api', 'backend'],
  },
  {
    id: 'nestjs-api',
    version: '1.0.0',
    title: 'NestJS API',
    description:
      'NestJS TypeScript API with multi-stage Dockerfile, K8s packaging, and pinned CI security stages.',
    icon: 'data_object',
    stack: 'nestjs',
    frameworks: ['nestjs'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 3000,
    tags: ['node', 'typescript', 'api', 'backend'],
  },
  {
    id: 'flask-api',
    version: '1.0.0',
    title: 'Flask API',
    description: 'Python Flask service with slim Dockerfile, K8s manifests, and Trivy/SAST CI.',
    icon: 'science',
    stack: 'flask',
    frameworks: ['flask'],
    default_tier: 'tier-3',
    default_slo: '99.0',
    listen_port: 5000,
    tags: ['python', 'api', 'backend'],
  },
  {
    id: 'django-api',
    version: '1.0.0',
    title: 'Django service',
    description:
      'Django app golden path with container scaffold, Kubernetes resources, and GitHub Actions security.',
    icon: 'dynamic_form',
    stack: 'django',
    frameworks: ['django'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 8000,
    tags: ['python', 'api', 'backend'],
  },
  {
    id: 'go-api',
    version: '1.0.0',
    title: 'Go API',
    description:
      'Go HTTP service with static binary Dockerfile, K8s Deployment/Service, and CI scanning.',
    icon: 'memory',
    stack: 'go',
    frameworks: ['go'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 8080,
    tags: ['go', 'api', 'backend'],
  },
  {
    id: 'springboot-api',
    version: '1.0.0',
    title: 'Spring Boot API',
    description:
      'Java Spring Boot service with multi-stage JVM image, K8s packaging, and CI security gates.',
    icon: 'coffee',
    stack: 'springboot',
    frameworks: ['springboot'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 8080,
    tags: ['java', 'api', 'backend'],
  },
  // --- Frontends ---
  {
    id: 'nextjs-web',
    version: '1.0.0',
    title: 'Next.js web app',
    description:
      'Next.js frontend with standalone/output Docker image, K8s Ingress-ready manifests, and CI/CD.',
    icon: 'web_asset',
    stack: 'nextjs',
    frameworks: ['nextjs'],
    default_tier: 'tier-3',
    default_slo: '99.0',
    listen_port: 3000,
    tags: ['frontend', 'nextjs', 'react', 'web'],
  },
  {
    id: 'nuxt-web',
    version: '1.0.0',
    title: 'Nuxt web app',
    description:
      'Nuxt frontend golden path with container scaffold, K8s Ingress-ready manifests, and GitHub workflow.',
    icon: 'web',
    stack: 'nuxtjs',
    frameworks: ['nuxtjs'],
    default_tier: 'tier-3',
    default_slo: '99.0',
    listen_port: 3000,
    tags: ['frontend', 'nuxt', 'vue', 'web'],
  },
  {
    id: 'react-vite-web',
    version: '1.0.0',
    title: 'React (Vite) web app',
    description:
      'React + Vite SPA with nginx static image, K8s Service, and CI security scanning.',
    icon: 'javascript',
    stack: 'react_vite',
    frameworks: ['react_vite'],
    default_tier: 'tier-3',
    default_slo: '99.0',
    listen_port: 80,
    tags: ['frontend', 'react', 'vite', 'web'],
  },
  {
    id: 'vue-web',
    version: '1.0.0',
    title: 'Vue web app',
    description: 'Vue SPA golden path with static nginx Dockerfile, K8s packaging, and CI/CD.',
    icon: 'view_quilt',
    stack: 'vuejs',
    frameworks: ['vuejs'],
    default_tier: 'tier-3',
    default_slo: '99.0',
    listen_port: 80,
    tags: ['frontend', 'vue', 'web'],
  },
  // --- Fullstack ---
  {
    id: 'fullstack-nuxt-fastapi',
    version: '1.1.0',
    title: 'Fullstack (Nuxt + FastAPI)',
    description:
      'Approved dual-stack: Nuxt UI + FastAPI API, shared docker-compose, K8s packaging, and CI/CD.',
    icon: 'dashboard',
    stack: 'nuxtjs',
    frameworks: ['nuxtjs', 'fastapi'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 3000,
    tags: ['fullstack', 'python', 'nuxt', 'node'],
    includes_iac: true,
  },
  {
    id: 'fullstack-nextjs-nestjs',
    version: '1.0.0',
    title: 'Fullstack (Next.js + NestJS)',
    description:
      'Next.js UI + NestJS API with dual Dockerfiles, compose, Kubernetes, and security-gated CI.',
    icon: 'hub',
    stack: 'nextjs',
    frameworks: ['nextjs', 'nestjs'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 3000,
    tags: ['fullstack', 'nextjs', 'nestjs', 'typescript'],
    includes_iac: true,
  },
  {
    id: 'fullstack-nuxt-nestjs',
    version: '1.0.0',
    title: 'Fullstack (Nuxt + NestJS)',
    description:
      'Nuxt frontend + NestJS backend golden path with multi-service compose and K8s manifests.',
    icon: 'account_tree',
    stack: 'nuxtjs',
    frameworks: ['nuxtjs', 'nestjs'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 3000,
    tags: ['fullstack', 'nuxt', 'nestjs', 'typescript'],
    includes_iac: true,
  },
  {
    id: 'fullstack-nextjs-fastapi',
    version: '1.0.0',
    title: 'Fullstack (Next.js + FastAPI)',
    description:
      'Next.js UI + FastAPI API - dual containers, K8s packaging, Terraform optional, CI security scans.',
    icon: 'join_inner',
    stack: 'nextjs',
    frameworks: ['nextjs', 'fastapi'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 3000,
    tags: ['fullstack', 'nextjs', 'python', 'fastapi'],
    includes_iac: true,
  },
  {
    id: 'fullstack-nextjs-express',
    version: '1.0.0',
    title: 'Fullstack (Next.js + Express)',
    description:
      'Next.js UI + Express API with dual Dockerfiles, docker-compose, K8s, and GitHub Actions.',
    icon: 'device_hub',
    stack: 'nextjs',
    frameworks: ['nextjs', 'express'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 3000,
    tags: ['fullstack', 'nextjs', 'express', 'node'],
    includes_iac: true,
  },
  {
    id: 'fullstack-nextjs-express-postgres',
    version: '1.0.0',
    title: 'Fullstack (Next.js + Express + PostgreSQL)',
    description:
      'Next.js UI + Express API + PostgreSQL database - dual containers, database provisioning, K8s packaging, and security CI.',
    icon: 'storage',
    stack: 'nextjs',
    frameworks: ['nextjs', 'express'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 3000,
    tags: ['fullstack', 'nextjs', 'express', 'postgres', 'node', 'database'],
    includes_iac: true,
    enable_postgres: true,
    enable_redis: false,
  },
  {
    id: 'fullstack-nextjs-express-postgres-redis',
    version: '1.0.0',
    title: 'Fullstack (Next.js + Express + PostgreSQL + Redis)',
    description:
      'Next.js UI + Express API + PostgreSQL + Redis cache - full-stack production architecture with database and cache provisioning.',
    icon: 'layers',
    stack: 'nextjs',
    frameworks: ['nextjs', 'express'],
    default_tier: 'tier-1',
    default_slo: '99.9',
    listen_port: 3000,
    tags: ['fullstack', 'nextjs', 'express', 'postgres', 'redis', 'node', 'cache'],
    includes_iac: true,
    enable_postgres: true,
    enable_redis: true,
  },
  {
    id: 'fullstack-nuxt-express',
    version: '1.0.0',
    title: 'Fullstack (Nuxt + Express)',
    description:
      'Nuxt UI + Express API golden path with compose, Kubernetes manifests, and CI scanning.',
    icon: 'lan',
    stack: 'nuxtjs',
    frameworks: ['nuxtjs', 'express'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 3000,
    tags: ['fullstack', 'nuxt', 'express', 'node'],
    includes_iac: true,
  },
  {
    id: 'fullstack-react-fastapi',
    version: '1.0.0',
    title: 'Fullstack (React + FastAPI)',
    description:
      'React/Vite SPA + FastAPI API with nginx + API containers, K8s, and security CI.',
    icon: 'widgets',
    stack: 'react_vite',
    frameworks: ['react_vite', 'fastapi'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 80,
    tags: ['fullstack', 'react', 'python', 'fastapi'],
    includes_iac: true,
  },
  {
    id: 'fullstack-vue-nestjs',
    version: '1.0.0',
    title: 'Fullstack (Vue + NestJS)',
    description:
      'Vue SPA + NestJS API with dual Dockerfiles, K8s packaging, and CI/CD security gates.',
    icon: 'grid_view',
    stack: 'vuejs',
    frameworks: ['vuejs', 'nestjs'],
    default_tier: 'tier-2',
    default_slo: '99.5',
    listen_port: 80,
    tags: ['fullstack', 'vue', 'nestjs', 'typescript'],
    includes_iac: true,
  },
];

function resolveTemplate(def: GoldenPathTemplateDef): GoldenPathTemplate {
  const includes_dockerfile = def.includes_dockerfile ?? true;
  const includes_k8s = def.includes_k8s ?? true;
  const includes_cicd = def.includes_cicd ?? true;
  const includes_iac = def.includes_iac ?? false;
  const enable_postgres = def.enable_postgres ?? false;
  const enable_redis = def.enable_redis ?? false;

  const images = dockerImagesForFrameworks(def.frameworks);
  if (enable_postgres && !images.includes('postgres:16-alpine')) {
    images.push('postgres:16-alpine');
  }
  if (enable_redis && !images.includes('redis:7-alpine')) {
    images.push('redis:7-alpine');
  }

  return {
    id: def.id,
    version: def.version,
    title: def.title,
    description: def.description,
    icon: def.icon,
    stack: def.stack,
    frameworks: [...def.frameworks],
    docker_images: images,
    default_tier: def.default_tier,
    default_slo: def.default_slo,
    listen_port: def.listen_port,
    tags: [...def.tags],
    includes_dockerfile,
    includes_k8s,
    includes_cicd,
    includes_iac,
    enable_postgres,
    enable_redis,
  };
}

const GOLDEN_PATH_TEMPLATES: GoldenPathTemplate[] = GOLDEN_PATH_TEMPLATE_DEFS.map(resolveTemplate);
const TEMPLATES_BY_ID = new Map(GOLDEN_PATH_TEMPLATES.map((t) => [t.id, t]));

function normalizeName(value: string): string {
  return value.trim().toLowerCase().replace(/_/g, '-');
}

/**
 * Simulated golden-path scorecard.
 *
 * The FastAPI control plane computes this from the scaffolded workspace files on
 * disk. The NestJS control plane is simulated (see MEMORY: Nest worker parity), so
 * it derives an equivalent scorecard from the template's declared golden-path
 * guarantees (hardened Dockerfile, security CI, K8s resources).
 */
function computeSimulatedScorecard(template: GoldenPathTemplate): ServiceScorecard {
  const items: ScorecardItem[] = [];

  const dockerOk = template.includes_dockerfile;
  items.push({
    id: 'dockerfile_hardened',
    title: 'Dockerfile non-root + slim/alpine base',
    passed: dockerOk,
    points: dockerOk ? 30 : 0,
    max_points: 30,
    detail: dockerOk ? 'Found hardened Dockerfile' : 'Missing USER non-root or slim base',
  });

  const ciOk = template.includes_cicd;
  items.push({
    id: 'ci_security',
    title: 'CI includes Trivy/SAST security scanning',
    passed: ciOk,
    points: ciOk ? 30 : 0,
    max_points: 30,
    detail: ciOk ? 'Security stages present' : 'No Trivy/SAST/CodeQL detected in CI',
  });

  const k8sOk = template.includes_k8s;
  items.push({
    id: 'k8s_resources',
    title: 'Kubernetes requests + limits set',
    passed: k8sOk,
    points: k8sOk ? 40 : 0,
    max_points: 40,
    detail: k8sOk ? 'Resource requests/limits present' : 'Missing requests/limits on workloads',
  });

  const score = items.reduce((total, item) => total + item.points, 0);
  const gate = 70;
  return { score, gate, passed: score >= gate, items };
}

@Injectable()
export class CatalogService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  getTemplates(): GoldenPathTemplate[] {
    return GOLDEN_PATH_TEMPLATES.map((t) => ({
      ...t,
      frameworks: [...t.frameworks],
      docker_images: [...t.docker_images],
      tags: [...t.tags],
    }));
  }

  /**
   * DB-backed list of catalog services visible to the caller.
   *
   * Mirrors FastAPI's `CatalogServiceManager.list_services`: services owned by the
   * user, plus (when the user has an org context) services in that org.
   */
  async getServices(user: CurrentUser): Promise<CatalogServiceRead[]> {
    const orgId = user.orgId;
    const rows = orgId
      ? await this.db
          .select()
          .from(catalogServices)
          .where(
            or(eq(catalogServices.orgId, orgId), eq(catalogServices.ownerId, user.userId)),
          )
          .orderBy(desc(catalogServices.createdAt))
      : await this.db
          .select()
          .from(catalogServices)
          .where(eq(catalogServices.ownerId, user.userId))
          .orderBy(desc(catalogServices.createdAt));
    return rows.map((row) => this.toRead(row));
  }

  async getService(serviceId: string, user: CurrentUser): Promise<CatalogServiceRead> {
    const row = await this.getRow(serviceId);
    if (!row || !this.canView(row, user)) {
      throw new NotFoundException({
        code: 'service_not_found',
        message: 'Catalog service not found',
      });
    }
    return this.toRead(row);
  }

  async createService(
    payload: CatalogServiceCreateDto,
    user: CurrentUser,
  ): Promise<CatalogServiceRead> {
    const template = TEMPLATES_BY_ID.get(payload.template_id);
    if (!template) {
      throw new NotFoundException({
        code: 'template_not_found',
        message: `Unknown golden path template: ${payload.template_id}`,
      });
    }

    const name = normalizeName(payload.name);

    const existing = await this.db
      .select()
      .from(catalogServices)
      .where(and(eq(catalogServices.ownerId, user.userId), eq(catalogServices.name, name)))
      .limit(1);
    if (existing.length > 0) {
      throw new ConflictException({
        code: 'service_exists',
        message: `Service '${name}' already exists`,
      });
    }

    const scorecard = computeSimulatedScorecard(template);
    const enforceGate = payload.enforce_scorecard_gate ?? true;
    if (enforceGate && !scorecard.passed) {
      const failed = scorecard.items.filter((item) => !item.passed).map((item) => item.title);
      throw new ConflictException({
        code: 'scorecard_gate_failed',
        message:
          `Service compliance score (${scorecard.score}/100) failed hard gate requirement ` +
          `(min ${scorecard.gate}). Failed checks: ${failed.join(', ')}`,
        scorecard,
      });
    }

    const now = new Date();
    const [inserted] = await this.db
      .insert(catalogServices)
      .values({
        id: randomUUID(),
        ownerId: user.userId,
        orgId: user.orgId ?? null,
        workspaceId: null,
        name,
        description: payload.description ?? '',
        serviceOwner: this.strip(payload.owner) ?? user.email,
        tier: payload.tier ?? 'tier-2',
        sloTarget: payload.slo_target ?? template.default_slo,
        runbookUrl: this.strip(payload.runbook_url ?? undefined) ?? null,
        onCall: this.strip(payload.on_call ?? undefined) ?? null,
        templateId: template.id,
        templateVersion: template.version,
        repositoryUrl: null,
        complianceScore: scorecard.score,
        scorecardJson: JSON.stringify(scorecard),
        createdAt: now,
        updatedAt: now,
      })
      .returning();

    return this.toRead(inserted);
  }

  async updateService(
    serviceId: string,
    payload: CatalogServiceUpdateDto,
    user: CurrentUser,
  ): Promise<CatalogServiceRead> {
    const row = await this.getRow(serviceId);
    if (!row || !this.canView(row, user)) {
      throw new NotFoundException({
        code: 'service_not_found',
        message: 'Catalog service not found',
      });
    }
    if (row.ownerId !== user.userId) {
      throw new ForbiddenException({
        code: 'forbidden',
        message: 'Only the service creator can update it',
      });
    }

    const updates: Partial<CatalogServiceRow> = { updatedAt: new Date() };
    if (payload.description !== undefined && payload.description !== null) {
      updates.description = payload.description;
    }
    if (payload.owner !== undefined && payload.owner !== null) {
      const cleaned = this.strip(payload.owner);
      if (cleaned) updates.serviceOwner = cleaned;
    }
    if (payload.tier !== undefined && payload.tier !== null) {
      updates.tier = payload.tier;
    }
    if (payload.slo_target !== undefined && payload.slo_target !== null) {
      updates.sloTarget = payload.slo_target;
    }
    if (payload.runbook_url !== undefined) {
      updates.runbookUrl = this.strip(payload.runbook_url ?? undefined) ?? null;
    }
    if (payload.on_call !== undefined) {
      updates.onCall = this.strip(payload.on_call ?? undefined) ?? null;
    }

    const [updated] = await this.db
      .update(catalogServices)
      .set(updates)
      .where(eq(catalogServices.id, serviceId))
      .returning();
    return this.toRead(updated);
  }

  async deleteService(serviceId: string, user: CurrentUser): Promise<void> {
    const row = await this.getRow(serviceId);
    if (!row || !this.canView(row, user)) {
      throw new NotFoundException({
        code: 'service_not_found',
        message: 'Catalog service not found',
      });
    }
    if (row.ownerId !== user.userId) {
      throw new ForbiddenException({
        code: 'forbidden',
        message: 'Only the service creator can delete it',
      });
    }
    await this.db.delete(catalogServices).where(eq(catalogServices.id, serviceId));
  }

  private async getRow(serviceId: string): Promise<CatalogServiceRow | undefined> {
    const [row] = await this.db
      .select()
      .from(catalogServices)
      .where(eq(catalogServices.id, serviceId))
      .limit(1);
    return row;
  }

  private canView(row: CatalogServiceRow, user: CurrentUser): boolean {
    if (row.ownerId === user.userId) return true;
    if (user.orgId && row.orgId === user.orgId) return true;
    return false;
  }

  private strip(value: string | undefined): string | null {
    if (value === undefined || value === null) return null;
    const cleaned = value.trim();
    return cleaned.length > 0 ? cleaned : null;
  }

  private toRead(row: CatalogServiceRow): CatalogServiceRead {
    let scorecard: ServiceScorecard;
    try {
      scorecard = JSON.parse(row.scorecardJson) as ServiceScorecard;
      if (!scorecard || typeof scorecard !== 'object' || !Array.isArray(scorecard.items)) {
        throw new Error('invalid scorecard');
      }
    } catch {
      scorecard = {
        score: row.complianceScore,
        gate: 70,
        passed: row.complianceScore >= 70,
        items: [],
      };
    }
    return {
      id: row.id,
      name: row.name,
      description: row.description,
      owner: row.serviceOwner,
      tier: row.tier,
      slo_target: row.sloTarget,
      runbook_url: row.runbookUrl,
      on_call: row.onCall,
      template_id: row.templateId,
      template_version: row.templateVersion,
      repository_url: row.repositoryUrl,
      workspace_id: row.workspaceId,
      compliance_score: row.complianceScore,
      scorecard,
      org_id: row.orgId,
      initial_preview_id: null,
      initial_preview_url: null,
      created_at: row.createdAt,
      updated_at: row.updatedAt,
    };
  }
}
