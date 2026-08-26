import { Injectable } from '@nestjs/common';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Generates runtime-appropriate infrastructure files for a saved workspace, mirroring
 * the shape FastAPI's generator produces (infra/k8s/manifests, docker-compose.yml,
 * infra/instance). Linked/imported repos previously saved with NO infra scaffolded;
 * this fills that gap so a workspace is deployable per its chosen runtime_mode.
 */

export interface ScaffoldService {
  name: string;
  port?: number | null;
  role?: string | null;
  kind?: string | null;
  health_path?: string | null;
  is_preview_target?: boolean | null;
}

export interface ScaffoldInput {
  durableDir: string;
  workspaceName: string;
  runtimeMode: string; // kubernetes | docker_compose | running_instance
  iacEngine?: string; // terraform | pulumi | ...
  enableIac?: boolean;
  services: ScaffoldService[];
  mountPrefix?: (name: string) => string; // for multi-repo apps/<name> build context
  datastores?: { kind: string; connection_url?: string }[];
}

@Injectable()
export class InfraScaffoldService {
  /** Write infra files for the chosen runtime; returns the relative paths written. */
  scaffold(input: ScaffoldInput): string[] {
    const services = input.services.length
      ? input.services
      : [{ name: input.workspaceName || 'app', port: 8080, role: 'web', is_preview_target: true }];
    const written: string[] = [];
    const write = (rel: string, content: string) => {
      const abs = path.join(input.durableDir, rel);
      fs.mkdirSync(path.dirname(abs), { recursive: true });
      fs.writeFileSync(abs, content.endsWith('\n') ? content : content + '\n', 'utf-8');
      written.push(rel);
    };

    const mode = (input.runtimeMode || 'kubernetes').toLowerCase();
    if (mode === 'kubernetes') {
      this.scaffoldKubernetes(input, services, write);
    } else if (mode === 'docker_compose') {
      this.scaffoldCompose(input, services, write);
    } else if (mode === 'running_instance') {
      this.scaffoldInstance(input, services, write);
    } else {
      this.scaffoldKubernetes(input, services, write);
    }

    // Optional IaC (terraform/pulumi) for non-kubernetes runtimes, matching FastAPI.
    if (input.enableIac && mode !== 'kubernetes') {
      const engine = (input.iacEngine || 'terraform').toLowerCase();
      if (engine === 'pulumi') {
        write('infra/pulumi/Pulumi.yaml', this.pulumiYaml(input.workspaceName));
        write('infra/pulumi/index.ts', this.pulumiIndex(input.workspaceName));
      } else {
        write('infra/terraform/main.tf', this.terraformMain(input.workspaceName));
        write('infra/terraform/variables.tf', this.terraformVariables(input.workspaceName));
      }
    }

    write('README.md', this.readme(input, services, mode));
    return written;
  }

  private slug(name: string): string {
    return (
      (name || 'app')
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, '-')
        .replace(/^-+|-+$/g, '') || 'app'
    );
  }

  private scaffoldKubernetes(
    input: ScaffoldInput,
    services: ScaffoldService[],
    write: (rel: string, content: string) => void,
  ): void {
    const ns = this.slug(input.workspaceName);
    write(
      'infra/k8s/manifests/namespace.yaml',
      `apiVersion: v1\nkind: Namespace\nmetadata:\n  name: ${ns}\n  labels:\n    launchpad.io/managed-by: launchpad-idp\n`,
    );
    for (const svc of services) {
      const s = this.slug(svc.name);
      const port = svc.port || 8080;
      write(
        `infra/k8s/manifests/${s}-deployment.yaml`,
        `apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${s}
  namespace: ${ns}
  labels:
    app: ${s}
    launchpad.io/managed-by: launchpad-idp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${s}
  template:
    metadata:
      labels:
        app: ${s}
    spec:
      containers:
        - name: ${s}
          image: REPLACE_WITH_IMAGE:latest
          ports:
            - containerPort: ${port}
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            tcpSocket:
              port: ${port}
            initialDelaySeconds: 5
            periodSeconds: 10
`,
      );
      write(
        `infra/k8s/manifests/${s}-service.yaml`,
        `apiVersion: v1
kind: Service
metadata:
  name: ${s}
  namespace: ${ns}
  labels:
    app: ${s}
    launchpad.io/managed-by: launchpad-idp
spec:
  selector:
    app: ${s}
  ports:
    - port: 80
      targetPort: ${port}
  type: ClusterIP
`,
      );
    }
    const preview = services.find((s) => s.is_preview_target) || services[0];
    const ps = this.slug(preview.name);
    write(
      'infra/k8s/manifests/ingress.yaml',
      `apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${ns}-ingress
  namespace: ${ns}
  labels:
    launchpad.io/managed-by: launchpad-idp
spec:
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${ps}
                port:
                  number: 80
`,
    );
  }

  private scaffoldCompose(
    input: ScaffoldInput,
    services: ScaffoldService[],
    write: (rel: string, content: string) => void,
  ): void {
    const lines: string[] = ['services:'];
    for (const svc of services) {
      const s = this.slug(svc.name);
      const port = svc.port || 8080;
      const ctx = input.mountPrefix ? input.mountPrefix(svc.name) : '.';
      const isFrontend = svc.kind === 'frontend' || ['frontend', 'web', 'ui', 'client'].some(k => s.includes(k));
      lines.push(`  ${s}:`);
      lines.push(`    build:`);
      lines.push(`      context: ${ctx || '.'}`);
      lines.push(`    ports:`);
      lines.push(`      - "${port}:${port}"`);
      if (!isFrontend) {
        lines.push(`    networks:`);
        lines.push(`      default:`);
        lines.push(`        aliases:`);
        lines.push(`          - api`);
        lines.push(`          - backend`);
        lines.push(`          - server`);
      }
      lines.push(`    restart: unless-stopped`);
    }
    
    if (input.datastores) {
      for (const ds of input.datastores) {
        if (ds.kind === 'postgres') {
          lines.push(`  postgres:`);
          lines.push(`    image: postgres:15-alpine`);
          lines.push(`    environment:`);
          lines.push(`      POSTGRES_USER: launchpad`);
          lines.push(`      POSTGRES_PASSWORD: launchpad`);
          lines.push(`      POSTGRES_DB: launchpad`);
          lines.push(`    ports:`);
          lines.push(`      - "5432:5432"`);
          lines.push(`    restart: unless-stopped`);
        } else if (ds.kind === 'redis') {
          lines.push(`  redis:`);
          lines.push(`    image: redis:7-alpine`);
          lines.push(`    ports:`);
          lines.push(`      - "6379:6379"`);
          lines.push(`    restart: unless-stopped`);
        }
      }
    }
    
    write('docker-compose.yml', lines.join('\n') + '\n');
  }

  private scaffoldInstance(
    input: ScaffoldInput,
    services: ScaffoldService[],
    write: (rel: string, content: string) => void,
  ): void {
    for (const svc of services) {
      const s = this.slug(svc.name);
      const port = svc.port || 8080;
      write(
        `infra/instance/${s}.service`,
        `[Unit]
Description=${s} (Launchpad)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/launchpad/${s}
Environment=PORT=${port}
ExecStart=/usr/bin/env sh -c 'echo "replace with your start command"'
Restart=always
RestartSec=5
User=app

[Install]
WantedBy=multi-user.target
`,
      );
    }
    const preview = services.find((s) => s.is_preview_target) || services[0];
    write(
      'infra/instance/nginx.conf',
      `server {
  listen 80;
  location / {
    proxy_pass http://127.0.0.1:${preview.port || 8080};
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
`,
    );
  }

  private terraformMain(name: string): string {
    return `terraform {\n  required_version = ">= 1.5.0"\n}\n\n# Workspace: ${name}\n# Define your cloud provider and resources here.\n`;
  }

  private terraformVariables(name: string): string {
    return `variable "environment_name" {
  type        = string
  default     = "${name}"
}

variable "project_id" {
  type        = string
  default     = ""
}

variable "app_listen_port" {
  type        = number
  default     = 8080
}

variable "app_image" {
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  type        = string
  default     = ""
}
`;
  }

  private pulumiYaml(name: string): string {
    return `name: ${this.slug(name)}\nruntime: nodejs\ndescription: Launchpad workspace ${name}\n`;
  }

  private pulumiIndex(name: string): string {
    return `// Pulumi program for workspace ${name}\n// Define your cloud resources here.\nexport const workspace = "${this.slug(name)}";\n`;
  }

  private readme(input: ScaffoldInput, services: ScaffoldService[], mode: string): string {
    const list = services.map((s) => `- ${s.name} (port ${s.port || 8080})`).join('\n');
    const runbook =
      mode === 'kubernetes'
        ? 'Apply manifests: `kubectl apply -f infra/k8s/manifests/`'
        : mode === 'docker_compose'
          ? 'Start services: `docker compose up -d`'
          : 'Install the systemd units under infra/instance/ and configure nginx.';
    return `# ${input.workspaceName}\n\nScaffolded by Launchpad (${mode}).\n\n## Services\n${list}\n\n## Deploy\n${runbook}\n`;
  }
}
