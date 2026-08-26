import { Injectable } from '@nestjs/common';
import * as fs from 'fs';
import * as path from 'path';

export interface DetectedServiceDto {
  id: string;
  name: string;
  path: string;
  role: 'web' | 'api' | 'worker' | 'unknown';
  framework: string;
  runtime: string;
  port: number;
  has_dockerfile: boolean;
  dockerfile_path?: string | null;
  env_hints: Record<string, string>;
  enabled: boolean;
  is_preview_target: boolean;
  health_path: string;
  markers: string[];
}

export interface DetectionResultDto {
  layout: 'monorepo' | 'single';
  monorepo_tools: string[];
  services: DetectedServiceDto[];
  datastores: string[];
  root_markers: string[];
  package_globs: string[];
  summary: string;
  has_kubernetes: boolean;
  has_compose: boolean;
  env_example: Array<{
    key: string;
    example_value: string;
    suggested_value: string;
    comment?: string;
    source: string;
    is_secret: boolean;
  }>;
}

const SKIP_DIRS = new Set([
  '.git',
  'node_modules',
  '.next',
  'dist',
  'build',
  'target',
  '.turbo',
  '.nx',
  'vendor',
  '__pycache__',
  '.venv',
  'venv',
  'coverage',
]);

@Injectable()
export class ProjectDetectorService {
  detect(rootDir: string): DetectionResultDto {
    const root = path.resolve(rootDir);
    if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
      throw new Error(`Repository root not found: ${rootDir}`);
    }

    const rootEntries = fs.readdirSync(root);
    const rootFiles = new Set(rootEntries.filter((e) => {
      try {
        return fs.statSync(path.join(root, e)).isFile();
      } catch {
        return false;
      }
    }));

    const tools = this.detectMonorepoTools(root, rootFiles);
    const packageDirs = this.discoverPackageDirs(root, rootEntries);
    const isMonorepo = tools.length > 0 || packageDirs.length > 1;

    const services: DetectedServiceDto[] = [];
    if (isMonorepo && packageDirs.length > 0) {
      for (const pDir of packageDirs) {
        const svc = this.classifyPackage(root, pDir);
        if (svc) services.push(svc);
      }
    } else {
      const svc = this.classifyPackage(root, root);
      if (svc) services.push(svc);
    }

    if (services.length === 0) {
      services.push({
        id: 'main',
        name: 'main',
        path: '.',
        role: 'api',
        framework: 'generic',
        runtime: 'unknown',
        port: 8080,
        has_dockerfile: fs.existsSync(path.join(root, 'Dockerfile')),
        dockerfile_path: fs.existsSync(path.join(root, 'Dockerfile')) ? 'Dockerfile' : null,
        env_hints: {},
        enabled: true,
        is_preview_target: true,
        health_path: '/',
        markers: [],
      });
    }

    // Designate first web app or first service as preview target
    const previewIdx = services.findIndex((s) => s.role === 'web');
    if (previewIdx !== -1) {
      services[previewIdx].is_preview_target = true;
    } else if (services.length > 0) {
      services[0].is_preview_target = true;
    }

    const datastores = this.detectDatastores(root);
    const hasKubernetes = fs.existsSync(path.join(root, 'k8s')) || fs.existsSync(path.join(root, 'kubernetes'));
    const hasCompose = fs.existsSync(path.join(root, 'docker-compose.yml')) || fs.existsSync(path.join(root, 'docker-compose.yaml'));
    const envExample = this.parseEnvExample(root);

    const layout = isMonorepo ? 'monorepo' : 'single';
    const summary = `${layout} · ${services.length} service(s)` +
      (datastores.length > 0 ? ` · datastores: ${datastores.join(', ')}` : '');

    return {
      layout,
      monorepo_tools: tools,
      services,
      datastores,
      root_markers: Array.from(rootFiles),
      package_globs: [],
      summary,
      has_kubernetes: hasKubernetes,
      has_compose: hasCompose,
      env_example: envExample,
    };
  }

  private detectMonorepoTools(root: string, rootFiles: Set<string>): string[] {
    const tools: string[] = [];
    if (fs.existsSync(path.join(root, 'pnpm-workspace.yaml'))) tools.push('pnpm');
    if (fs.existsSync(path.join(root, 'lerna.json'))) tools.push('lerna');
    if (fs.existsSync(path.join(root, 'turbo.json'))) tools.push('turbo');
    if (fs.existsSync(path.join(root, 'nx.json'))) tools.push('nx');
    if (fs.existsSync(path.join(root, 'Cargo.toml')) && fs.existsSync(path.join(root, 'Cargo.lock'))) {
      const content = fs.readFileSync(path.join(root, 'Cargo.toml'), 'utf-8');
      if (content.includes('[workspace]')) tools.push('cargo');
    }
    if (fs.existsSync(path.join(root, 'go.work'))) tools.push('go_work');

    if (rootFiles.has('package.json')) {
      try {
        const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf-8'));
        if (pkg.workspaces) tools.push('npm_workspaces');
      } catch {}
    }

    return Array.from(new Set(tools));
  }

  private discoverPackageDirs(root: string, rootEntries: string[]): string[] {
    const pkgDirs: string[] = [];
    const subDirsToScan = ['apps', 'packages', 'services'];

    for (const subDir of subDirsToScan) {
      const target = path.join(root, subDir);
      if (fs.existsSync(target) && fs.statSync(target).isDirectory()) {
        try {
          const children = fs.readdirSync(target);
          for (const child of children) {
            const fullChild = path.join(target, child);
            if (!SKIP_DIRS.has(child) && fs.statSync(fullChild).isDirectory()) {
              pkgDirs.push(fullChild);
            }
          }
        } catch {}
      }
    }
    return pkgDirs;
  }

  private classifyPackage(root: string, pkgDir: string): DetectedServiceDto | null {
    const relPath = path.relative(root, pkgDir) || '.';
    const nameSlug = relPath === '.' ? path.basename(root) : relPath.replace(/\//g, '-');
    const safeName = nameSlug.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '') || 'app';

    let role: 'web' | 'api' | 'worker' | 'unknown' = 'unknown';
    let framework = 'generic';
    let runtime = 'unknown';
    let port = 8080;
    let healthPath = '/';

    const hasDockerfile = fs.existsSync(path.join(pkgDir, 'Dockerfile'));

    if (fs.existsSync(path.join(pkgDir, 'package.json'))) {
      runtime = 'nodejs';
      try {
        const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf-8'));
        const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };

        if (deps['next']) {
          role = 'web';
          framework = 'nextjs';
          port = 3000;
        } else if (deps['nuxt']) {
          role = 'web';
          framework = 'nuxtjs';
          port = 3000;
        } else if (deps['@nestjs/core']) {
          role = 'api';
          framework = 'nestjs';
          port = 3000;
          healthPath = '/health';
        } else if (deps['express'] || deps['fastify']) {
          role = 'api';
          framework = deps['express'] ? 'express' : 'fastify';
          port = 3000;
        } else if (deps['vite'] || deps['vue'] || deps['react']) {
          role = 'web';
          framework = 'vite';
          port = 5173;
        }
      } catch {}
    } else if (fs.existsSync(path.join(pkgDir, 'requirements.txt')) || fs.existsSync(path.join(pkgDir, 'pyproject.toml'))) {
      runtime = 'python';
      role = 'api';
      port = 8000;
      framework = 'fastapi';
    } else if (fs.existsSync(path.join(pkgDir, 'go.mod'))) {
      runtime = 'go';
      role = 'api';
      port = 8080;
      framework = 'go';
    } else if (fs.existsSync(path.join(pkgDir, 'Cargo.toml'))) {
      runtime = 'rust';
      role = 'api';
      port = 8080;
      framework = 'rust';
    }

    if (role === 'unknown' && !hasDockerfile) {
      return null;
    }

    return {
      id: safeName,
      name: safeName,
      path: relPath,
      role,
      framework,
      runtime,
      port,
      has_dockerfile: hasDockerfile,
      dockerfile_path: hasDockerfile ? path.join(relPath, 'Dockerfile') : null,
      env_hints: {},
      enabled: true,
      is_preview_target: false,
      health_path: healthPath,
      markers: [],
    };
  }

  private detectDatastores(root: string): string[] {
    const datastores: string[] = [];
    const checkFile = (filePath: string) => {
      if (!fs.existsSync(filePath)) return;
      const text = fs.readFileSync(filePath, 'utf-8').toLowerCase();
      if (text.includes('postgres') || text.includes('postgresql')) datastores.push('postgres');
      if (text.includes('redis')) datastores.push('redis');
      if (text.includes('mongo') || text.includes('mongodb')) datastores.push('mongodb');
      if (text.includes('mysql') || text.includes('mariadb')) datastores.push('mysql');
    };

    checkFile(path.join(root, 'docker-compose.yml'));
    checkFile(path.join(root, 'docker-compose.yaml'));
    checkFile(path.join(root, '.env.example'));

    return Array.from(new Set(datastores));
  }

  private parseEnvExample(root: string): Array<{
    key: string;
    example_value: string;
    suggested_value: string;
    comment?: string;
    source: string;
    is_secret: boolean;
  }> {
    const envPath = path.join(root, '.env.example');
    if (!fs.existsSync(envPath)) return [];

    const lines = fs.readFileSync(envPath, 'utf-8').split('\n');
    const result = [];
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const idx = trimmed.indexOf('=');
      if (idx !== -1) {
        const key = trimmed.substring(0, idx).trim();
        const example = trimmed.substring(idx + 1).trim();
        const isSecret = key.toLowerCase().includes('secret') || key.toLowerCase().includes('key') || key.toLowerCase().includes('password');
        result.push({
          key,
          example_value: example,
          suggested_value: example,
          source: '.env.example',
          is_secret: isSecret,
        });
      }
    }
    return result;
  }
}
