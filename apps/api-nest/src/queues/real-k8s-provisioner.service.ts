import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { promisify } from 'node:util';

import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

import { GithubAppService } from '../provisioning/github-app.service';
import { UserCredentialsService } from '../user-credentials/user-credentials.service';
import { CloudKubeconfigService } from './cloud-kubeconfig.service';

const execFileAsync = promisify(execFile);

// Publish the k8s NodePort range [30000,30099] from the k3d server node to localhost, so
// local-dev previews exposed as NodePort are reachable at http://localhost:<nodePort>.
const K3D_NODEPORT_MAP = ['--port', '30000-30099:30000-30099@server:0'];

export interface RealK8sResult {
  applied: boolean;
  previewUrl: string | null;
  namespace: string;
  context: string;
  detail: string;
}

/**
 * Phase 1 of the real (non-simulated) provisioning port: apply a workspace's scaffolded
 * Kubernetes manifests to a REAL cluster via kubectl, wait for rollout, and resolve the
 * real preview URL (LoadBalancer IP / ingress host / wildcard ingress). Faithful subset
 * of FastAPI manifest_deploy: context selection, namespace, apply, rollout, URL.
 *
 * NOT yet ported (later phases, clearly gated): building the app image from source and
 * pushing to GAR/ECR (Phase 1 deploys a configurable pullable image so rollout completes
 * against a real cluster), cloud cluster CREATION/terraform, and compose/instance real
 * execution. Cloud cluster CONNECTION uses the kubeconfig context (populate it out of band
 * with `gcloud/aws/az ... get-credentials`); we never create billable cloud resources here.
 */
@Injectable()
export class RealK8sProvisionerService {
  private readonly logger = new Logger(RealK8sProvisionerService.name);

  constructor(
    private readonly config: ConfigService,
    private readonly userCreds: UserCredentialsService,
    private readonly cloudKube: CloudKubeconfigService,
    private readonly githubApp: GithubAppService,
  ) {}

  /** True when real Kubernetes mode is enabled (KUBERNETES_ENABLED truthy). */
  get enabled(): boolean {
    const raw = (this.config.get<string>('KUBERNETES_ENABLED') ?? '').trim().toLowerCase();
    return raw === '1' || raw === 'true' || raw === 'yes' || raw === 'on';
  }

  private cfg(key: string): string {
    return (this.config.get<string>(key) ?? '').trim();
  }

  /**
   * Resolve the kubeconfig context. Precedence: per-provider override (KUBE_CONTEXT_<P>),
   * then KUBERNETES_CONTEXT (the SAME var FastAPI uses - keeps both backends pointed at the
   * same cluster, e.g. k3d-launchpad), then KUBE_CONTEXT, then kubectl current-context.
   */
  private async resolveContext(provider: string | null | undefined): Promise<string> {
    const p = (provider || 'local').toLowerCase();
    const explicit =
      this.cfg(`KUBE_CONTEXT_${p.toUpperCase()}`) ||
      this.cfg('KUBERNETES_CONTEXT') ||
      this.cfg('KUBE_CONTEXT');
    if (explicit) return explicit;
    try {
      const { stdout } = await execFileAsync('kubectl', ['config', 'current-context'], {
        timeout: 10000,
      });
      return stdout.trim();
    } catch {
      return '';
    }
  }

  /**
   * Quick reachability probe for the cluster's API server. Mirrors FastAPI's
   * KubernetesProvisioner.available(): when the configured cluster is not reachable we do
   * NOT hard-fail the provision - the caller falls back to the simulated path (env RUNNING
   * with a wildcard-ingress or localhost URL), like FastAPI does when its kubeconfig is unreachable.
   */
  private async isClusterReachable(context: string, env: Record<string, string> = {}): Promise<boolean> {
    try {
      await execFileAsync(
        'kubectl',
        ['--context', context, 'get', '--raw', '/healthz', '--request-timeout=8s'],
        { timeout: 12000, env: { ...process.env, ...env } },
      );
      return true;
    } catch {
      return false;
    }
  }

  private slug(name: string): string {
    return (
      (name || 'app')
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, '-')
        .replace(/^-+|-+$/g, '') || 'app'
    );
  }

  /**
   * True when preview URLs should use the wildcard homelab host ws-{id}.{base} (Cloudflare
   * Tunnel -> in-cluster ingress). Mirrors FastAPI Settings.preview_tunnel_active: requires
   * PREVIEW_BASE_DOMAIN AND (USE_CLOUDFLARE_TUNNEL truthy OR ENVIRONMENT=production). On a
   * plain local dev box (tunnel off) this is false, so we must NOT emit ws-* (it resolves to
   * the homelab, not the dev machine) - use a localhost NodePort URL instead.
   */
  private previewTunnelActive(): boolean {
    if (!this.cfg('PREVIEW_BASE_DOMAIN')) return false;
    const t = this.cfg('USE_CLOUDFLARE_TUNNEL').toLowerCase();
    const on = t === '1' || t === 'true' || t === 'yes' || t === 'on';
    return on || this.cfg('ENVIRONMENT').toLowerCase() === 'production';
  }

  /** Deterministic NodePort in the k3d/kind-mapped range [30000,30099] for local previews. */
  private localNodePort(envId: string): number {
    const h = parseInt(envId.replace(/-/g, '').slice(0, 6), 16) || 0;
    return 30000 + (h % 100);
  }

  /**
   * Expose the preview service as a NodePort reachable from localhost (local dev, no tunnel).
   * Reads the service's port/targetPort, then patches it to NodePort with a fixed nodePort in
   * the cluster's mapped range. Returns the nodePort, or null on failure.
   */
  private async exposeLocalNodePort(
    context: string,
    namespace: string,
    service: string,
    envId: string,
    env: Record<string, string> = {},
  ): Promise<number | null> {
    try {
      const { stdout } = await execFileAsync(
        'kubectl',
        [
          '--context',
          context,
          '-n',
          namespace,
          'get',
          'svc',
          service,
          '-o',
          'jsonpath={.spec.ports[0].port}{" "}{.spec.ports[0].targetPort}',
        ],
        { timeout: 15000, env: { ...process.env, ...env } },
      );
      const [port, targetPort] = stdout.trim().split(/\s+/);
      const nodePort = this.localNodePort(envId);
      const patch = JSON.stringify({
        spec: {
          type: 'NodePort',
          ports: [
            {
              port: Number.parseInt(port || '80', 10) || 80,
              targetPort: Number.parseInt(targetPort || '8080', 10) || targetPort || 8080,
              nodePort,
            },
          ],
        },
      });
      await this.kubectl(context, ['-n', namespace, 'patch', 'svc', service, '-p', patch], {
        ignoreError: true,
        env,
      });
      return nodePort;
    } catch {
      return null;
    }
  }

  /**
   * Create a local k3d/kind cluster on demand ("created on launch"), inferring the tool from
   * the context prefix (k3d-<name> -> k3d, kind-<name> -> kind). This is a REAL cluster, not
   * simulation. Best-effort: logs and continues if creation fails (the reachability gate then
   * fails the provision with a clear message).
   */
  private async ensureLocalCluster(context: string): Promise<void> {
    let tool: 'k3d' | 'kind';
    let name: string;
    if (context.startsWith('k3d-')) {
      tool = 'k3d';
      name = context.slice(4);
    } else if (context.startsWith('kind-')) {
      tool = 'kind';
      name = context.slice(5);
    } else {
      // Unknown local context naming; default to k3d with the trailing segment as the name.
      tool = 'k3d';
      name = context.replace(/^[^-]*-/, '') || 'launchpad';
    }
    this.logger.log(`real_k8s: ensuring local ${tool} cluster '${name}' (context ${context})`);
    if (tool === 'k3d') {
      // Map the NodePort range [30000,30099] on the server node to localhost so local-dev
      // previews (exposed as NodePort) are reachable at http://localhost:<nodePort>.
      const k3dCreate = ['cluster', 'create', name, '--wait', ...K3D_NODEPORT_MAP];
      // Fresh create; --wait blocks until the API server is ready.
      const created = await this.exec('k3d', k3dCreate, 300000, true);
      if (!created) {
        // Cluster registration already exists: try to start it and merge its kubeconfig.
        await this.exec('k3d', ['cluster', 'start', name], 120000, true);
        await this.exec(
          'k3d',
          ['kubeconfig', 'merge', name, '--kubeconfig-merge-default', '--kubeconfig-switch-context=false'],
          30000,
          true,
        );
        // Still not reachable => the existing cluster is broken/partial: delete + recreate.
        if (!(await this.isClusterReachable(context))) {
          this.logger.warn(`real_k8s: local k3d cluster '${name}' is broken - recreating`);
          await this.exec('k3d', ['cluster', 'delete', name], 120000, true);
          await this.exec('k3d', k3dCreate, 300000, true);
        }
      }
    } else {
      // kind needs the port mappings baked into a cluster config at creation time.
      const kindConfig = this.writeKindConfig();
      const kindCreate = ['create', 'cluster', '--name', name, '--wait', '120s', '--config', kindConfig];
      const created = await this.exec('kind', kindCreate, 300000, true);
      if (!created && !(await this.isClusterReachable(context))) {
        this.logger.warn(`real_k8s: local kind cluster '${name}' is broken - recreating`);
        await this.exec('kind', ['delete', 'cluster', '--name', name], 120000, true);
        await this.exec('kind', kindCreate, 300000, true);
      }
    }
  }

  /** Write a kind cluster config mapping the [30000,30099] NodePort range to the host. */
  private writeKindConfig(): string {
    const mappings: string[] = [];
    for (let p = 30000; p <= 30099; p += 1) {
      mappings.push(`  - containerPort: ${p}\n    hostPort: ${p}\n    protocol: TCP`);
    }
    const body =
      'kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nnodes:\n- role: control-plane\n  extraPortMappings:\n' +
      mappings.join('\n') +
      '\n';
    const file = path.join(os.tmpdir(), 'launchpad-kind-config.yaml');
    fs.writeFileSync(file, body, 'utf8');
    return file;
  }

  /**
   * Provision an environment onto a real cluster from its workspace's k8s manifests.
   * Returns null when real k8s is not applicable (disabled, non-k8s runtime, or no
   * manifests) so the caller can fall back to the simulated pipeline.
   */
  async provision(env: {
    id: string;
    name: string;
    provider?: string | null;
    namespace?: string | null;
    ownerId?: string | null;
    cloud?: Record<string, any> | null;
    workspaceRootDir?: string | null;
    runtimeMode?: string | null;
  }): Promise<RealK8sResult | null> {
    // NO SIMULATION: every provision must be a REAL cluster deploy. If it can't, throw so the
    // env goes FAILED with a clear reason (applies to local and cloud alike).
    const isCloud = Boolean(env.provider && env.provider !== 'local');
    const cannotDeploy = (reason: string): never => {
      throw new Error(
        isCloud ? `Cloud deploy requires a real cluster: ${reason}` : `Real deploy failed: ${reason}`,
      );
    };

    if (!this.enabled) cannotDeploy('KUBERNETES_ENABLED is off');
    const runtime = (env.runtimeMode || 'kubernetes').toLowerCase();
    if (runtime !== 'kubernetes') {
      // Real docker_compose / running_instance execution is not implemented; with simulation
      // disabled we must not fake a RUNNING env, so fail clearly.
      cannotDeploy(`real execution for runtime '${runtime}' is not implemented yet`);
    }
    const root = env.workspaceRootDir;
    if (!root) return cannotDeploy('workspace has no source/manifests directory');
    // Prefer manifests the REPO ships (k8s/, kubernetes/, manifests/, ...) over the
    // Launchpad-scaffolded infra/k8s/manifests, so a repo that already defines its own
    // Deployment/Service is deployed as-is (mirrors FastAPI's manifest-deploy preference).
    const manifestsDir = this.resolveManifestDir(root);
    if (!manifestsDir) return cannotDeploy('no Kubernetes manifests found in the workspace');
    const files = this.collectManifestFiles(manifestsDir);
    if (files.length === 0) return cannotDeploy('no Kubernetes manifests found in the workspace');

    // Acquire the target cluster. For CLOUD, fetch a kubeconfig context from the user's
    // stored cloud credentials (gcloud/aws/az get-credentials), like FastAPI - this is what
    // makes cloud deploys work without a pre-baked context. `runEnv` carries the isolated
    // KUBECONFIG/auth applied to every subsequent cluster command.
    let context: string;
    let runEnv: Record<string, string> = {};
    let cloudCleanup: (() => void) | null = null;
    if (isCloud) {
      const creds = env.ownerId ? await this.userCreds.getCredentials(env.ownerId) : {};
      const target = await this.cloudKube.acquire(env.provider!, creds, env.cloud ?? null, env.id);
      if (!target) return cannotDeploy('unsupported cloud provider or missing cloud credentials');
      context = target.context;
      runEnv = target.env;
      cloudCleanup = target.cleanup;
    } else {
      context = await this.resolveContext(env.provider);
      if (!context) return cannotDeploy('no kube context configured (set KUBERNETES_CONTEXT)');
      // LOCAL: create the cluster on demand (kind/k3d) if it is not running - "created on
      // launch". This is a REAL cluster (not simulation). Enabled by default; disable with
      // LOCAL_CLUSTER_AUTO_CREATE=false.
      if (!(await this.isClusterReachable(context))) {
        const autoRaw = this.cfg('LOCAL_CLUSTER_AUTO_CREATE').toLowerCase();
        const auto = autoRaw === '' ? true : ['1', 'true', 'yes', 'on'].includes(autoRaw);
        if (auto) await this.ensureLocalCluster(context);
      }
    }
    // Final reachability gate: cloud -> fail; local -> fail if the cluster still isn't up
    // (creation failed). No simulation either way.
    if (!(await this.isClusterReachable(context, runEnv))) {
      cloudCleanup?.();
      return cannotDeploy(
        `cluster '${context}' is unreachable (control-plane blocked/authorized-networks, ` +
          `or the local cluster could not be created) - run the worker where the cluster is reachable`,
      );
    }
    // Use the environment's stable stored namespace so teardown targets the SAME one
    // even after the env is renamed on delete (release_unique_identity). Never derive
    // from the (mutable) name here.
    const namespace = this.namespaceFor(env);
    const defaultImage =
      this.cfg('REAL_PREVIEW_DEFAULT_IMAGE') || 'nginxdemos/hello:plain-text';

    // Linked workspaces persist only the scaffolded infra/ (no app source on disk), so make
    // sure the repo source is checked out before building - otherwise there is nothing to
    // build and the deploy would fall back to a placeholder image that never serves the app.
    const sourceRoot = await this.ensureSourceCheckout(root, env.cloud ?? null, env.ownerId ?? null);

    // Phase 2: build the app image(s) from the workspace source and stage them where the
    // cluster can pull (kind load for local; Artifact Registry push for cloud). When no
    // Dockerfile is found (or docker/registry unavailable) this returns empty and the
    // scaffolded placeholder falls back to REAL_PREVIEW_DEFAULT_IMAGE.
    const built = await this.buildAndStageImages(sourceRoot, context, env.provider, env.id, runEnv);

    const applyDir = fs.mkdtempSync(path.join(os.tmpdir(), `lp-k8s-${env.id.slice(0, 8)}-`));
    try {
      let idx = 0;
      for (const file of files) {
        // Force every object into the env namespace and drop any Namespace objects
        // (we create the target namespace ourselves), so `kubectl apply -n <ns>` never
        // conflicts with a namespace baked into the manifest.
        const rendered = this.renderManifest(fs.readFileSync(file, 'utf-8'), namespace, built, defaultImage);
        if (!rendered.trim()) continue;
        fs.writeFileSync(
          path.join(applyDir, `${String(idx++).padStart(3, '0')}-${path.basename(file)}`),
          rendered,
          'utf-8',
        );
      }

      await this.kubectl(context, ['create', 'namespace', namespace], { ignoreError: true, env: runEnv });
      // --validate=false skips kubectl's client-side OpenAPI download, which can i/o-timeout
      // against a remote (e.g. GKE) API endpoint; the server still validates on apply.
      await this.kubectl(context, ['apply', '--validate=false', '-n', namespace, '-f', applyDir], {
        env: runEnv,
      });

      // Wait for every Deployment to become Available. Fail FAST and DETERMINISTICALLY:
      // if a rollout doesn't complete (commonly ImagePullBackOff when the app image is
      // not built/pushed yet), throw a clear error so the env goes FAILED instead of
      // hanging until the stale watchdog reaps it (or falsely reporting RUNNING). The
      // catch below tears down the namespace so no billable pods are left running.
      const deployments = await this.listDeployments(context, namespace, runEnv);

      // The scaffold can remap a service's containerPort (e.g. two services both detected on
      // 3000 get bumped to 3200/3220 for uniqueness), but the app inside the image still binds
      // its framework default. Inject PORT=<containerPort> so PORT-honoring runtimes (Next.js,
      // Express, uvicorn/gunicorn started from $PORT) actually listen where the Service
      // targetPort and readiness probe point. Best-effort; harmless when the app ignores PORT.
      await this.injectPortEnv(context, namespace, runEnv);

      // Wait for all Deployments to become Available by POLLING. ContainerCreating / Pending /
      // PodInitializing are TRANSIENT (image pull, node scale-up) - we keep waiting through
      // them. We fail FAST only on TERMINAL pod states (image can't pull, config/crash), and
      // otherwise fail on timeout. Cloud (GKE/EKS/AKS) node scale-up is slow, so the default
      // is generous and configurable via REAL_PREVIEW_ROLLOUT_TIMEOUT (seconds).
      const rolloutTimeout = Number.parseInt(this.cfg('REAL_PREVIEW_ROLLOUT_TIMEOUT'), 10) || 300;
      const TERMINAL =
        /ImagePullBackOff|ErrImagePull|InvalidImageName|ErrImageNeverPull|CrashLoopBackOff|CreateContainerConfigError|CreateContainerError|RunContainerError/i;
      const deadline = Date.now() + rolloutTimeout * 1000;
      for (;;) {
        const notReady = await this.notReadyDeployments(context, namespace, runEnv);
        if (notReady.length === 0) break; // all deployments Available
        const reason = await this.podFailureReason(context, namespace, runEnv);
        if (TERMINAL.test(reason)) {
          throw new Error(
            `Deployment(s) ${notReady.join(', ')} did not become ready (${reason}). ` +
              'If this is an app image that has not been built/pushed, that is the Phase 2 gap.',
          );
        }
        if (Date.now() >= deadline) {
          throw new Error(
            `Deployment(s) ${notReady.join(', ')} did not become ready within ${rolloutTimeout}s` +
              `${reason ? ` (last state: ${reason})` : ''}.`,
          );
        }
        await new Promise((r) => setTimeout(r, 5000));
      }

      // Cloud providers: expose the preview service as a LoadBalancer so the cloud
      // (GKE/EKS/AKS) allocates a real public IP - matching FastAPI, which returns the
      // LoadBalancer IP (http://34.x.x.x) for cloud deploys rather than the ws-* host.
      const disableLb = ['1', 'true', 'yes', 'on'].includes(
        this.cfg('REAL_PREVIEW_DISABLE_LB').toLowerCase(),
      );
      if (isCloud && !disableLb) {
        const previewSvc = this.previewServiceName(applyDir);
        if (previewSvc) {
          await this.kubectl(
            context,
            ['-n', namespace, 'patch', 'svc', previewSvc, '-p', '{"spec":{"type":"LoadBalancer"}}'],
            { ignoreError: true, env: runEnv },
          );
          await this.waitForLoadBalancerIp(context, namespace, previewSvc, runEnv);
        }
      }

      // Local dev (no Cloudflare tunnel): expose the preview service as a NodePort mapped to
      // localhost so the preview URL is actually reachable on the dev machine. We must NOT use
      // the ws-{id}.{base} homelab host here (it routes to the homelab cluster, not this box).
      let localNodePort: number | null = null;
      if (!isCloud && !this.previewTunnelActive()) {
        const previewSvc = this.previewServiceName(applyDir);
        if (previewSvc) {
          localNodePort = await this.exposeLocalNodePort(
            context,
            namespace,
            previewSvc,
            env.id,
            runEnv,
          );
        }
      }

      const previewUrl = await this.resolveUrl(env, context, namespace, runEnv, localNodePort);
      this.logger.log(
        `real_k8s_provisioned env=${env.id} ctx=${context} ns=${namespace} url=${previewUrl}`,
      );
      return {
        applied: true,
        previewUrl,
        namespace,
        context,
        detail: `Applied ${deployments.length} deployment(s) to ${context}/${namespace}`,
      };
    } catch (err) {
      // Provision failed: delete this env's namespace so we never leave billable pods /
      // LoadBalancers running on the cloud. Best-effort; then rethrow so the env -> FAILED.
      await this.kubectl(context, ['delete', 'namespace', namespace, '--wait=false'], {
        ignoreError: true,
        env: runEnv,
      });
      throw err;
    } finally {
      fs.rmSync(applyDir, { recursive: true, force: true });
      cloudCleanup?.();
    }
  }

  /** Summarize why pods aren't ready (ImagePullBackOff, CrashLoopBackOff, ...) for a clear error. */
  private async podFailureReason(
    context: string,
    namespace: string,
    env: Record<string, string> = {},
  ): Promise<string> {
    try {
      const { stdout } = await execFileAsync(
        'kubectl',
        [
          '--context',
          context,
          '-n',
          namespace,
          'get',
          'pods',
          '-o',
          'jsonpath={range .items[*]}{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}{end}',
        ],
        { timeout: 15000, env: { ...process.env, ...env } },
      );
      const reasons = Array.from(new Set(stdout.trim().split(/\s+/).filter(Boolean)));
      return reasons.join(', ');
    } catch {
      return '';
    }
  }

  /**
   * Stable namespace for an environment. Prefers the env's stored namespace_name (set at
   * create and NOT renamed on delete), falling back to an id-derived namespace. Must be
   * identical at provision and teardown so we never orphan real cloud resources.
   */
  private namespaceFor(env: { id: string; namespace?: string | null }): string {
    const stored = (env.namespace || '').trim();
    if (stored && !stored.startsWith('destroyed-')) return this.slug(stored);
    return `lp-${env.id.replace(/-/g, '').slice(0, 12)}`;
  }

  /** Tear down the environment's namespace from the real cluster (deletes all workloads). */
  async teardown(env: {
    id: string;
    name: string;
    provider?: string | null;
    namespace?: string | null;
    ownerId?: string | null;
    cloud?: Record<string, any> | null;
  }): Promise<void> {
    if (!this.enabled) return;
    const isCloud = Boolean(env.provider && env.provider !== 'local');
    const namespace = this.namespaceFor(env);
    let context: string;
    let runEnv: Record<string, string> = {};
    let cleanup: (() => void) | null = null;
    try {
      if (isCloud) {
        // Re-acquire cloud cluster access from stored creds so we delete the REAL namespace.
        const creds = env.ownerId ? await this.userCreds.getCredentials(env.ownerId) : {};
        const target = await this.cloudKube.acquire(env.provider!, creds, env.cloud ?? null, env.id);
        if (!target) return;
        context = target.context;
        runEnv = target.env;
        cleanup = target.cleanup;
      } else {
        context = await this.resolveContext(env.provider);
        if (!context) return;
      }
      await this.kubectl(context, ['delete', 'namespace', namespace, '--wait=false'], {
        ignoreError: true,
        env: runEnv,
      });
    } catch (err) {
      this.logger.warn(`real_k8s teardown failed for ${env.id}: ${(err as Error).message}`);
    } finally {
      cleanup?.();
    }
  }

  /**
   * Locate the Kubernetes manifest directory. Repo-shipped manifests win over the
   * Launchpad-scaffolded ones. For multi-repo workspaces (apps/<name>/), repo dirs are
   * checked under each app too. Returns null when nothing manifest-like is found.
   */
  private resolveManifestDir(root: string): string | null {
    const repoDirs = ['k8s', 'kubernetes', 'manifests', '.k8s', 'deploy/k8s', 'deploy'];
    const roots = [root];
    const appsDir = path.join(root, 'apps');
    if (fs.existsSync(appsDir) && fs.statSync(appsDir).isDirectory()) {
      for (const app of fs.readdirSync(appsDir)) {
        roots.push(path.join(appsDir, app));
      }
    }
    for (const base of roots) {
      for (const d of repoDirs) {
        const candidate = path.join(base, d);
        if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
          if (this.collectManifestFiles(candidate).length > 0) return candidate;
        }
      }
    }
    // Fallback: Launchpad-scaffolded manifests.
    const scaffolded = path.join(root, 'infra', 'k8s', 'manifests');
    if (fs.existsSync(scaffolded)) return scaffolded;
    return null;
  }

  /** Collect *.yaml/*.yml files (this dir + one level of subdirs) that contain a k8s `kind:`. */
  private collectManifestFiles(dir: string): string[] {
    const out: string[] = [];
    const scan = (d: string) => {
      let entries: fs.Dirent[];
      try {
        entries = fs.readdirSync(d, { withFileTypes: true });
      } catch {
        return;
      }
      for (const e of entries) {
        const full = path.join(d, e.name);
        if (e.isFile() && /\.(ya?ml)$/i.test(e.name)) {
          try {
            if (/\bkind:\s*\S+/.test(fs.readFileSync(full, 'utf-8'))) out.push(full);
          } catch {
            // skip unreadable
          }
        }
      }
      // one level of subdirectories (e.g. base/, overlays/)
      for (const e of entries) {
        if (e.isDirectory()) {
          try {
            for (const f of fs.readdirSync(path.join(d, e.name), { withFileTypes: true })) {
              if (f.isFile() && /\.(ya?ml)$/i.test(f.name)) {
                const full = path.join(d, e.name, f.name);
                if (/\bkind:\s*\S+/.test(fs.readFileSync(full, 'utf-8'))) out.push(full);
              }
            }
          } catch {
            // skip
          }
        }
      }
    };
    scan(dir);
    return out.sort();
  }

  /**
   * Render a (possibly multi-doc) manifest for a preview deploy: drop Namespace objects
   * (we create the target namespace), force every object's metadata.namespace to the env
   * namespace, and substitute the placeholder image. Keeps everything in one namespace so
   * `kubectl apply -n <ns>` never conflicts with a baked-in namespace.
   */
  private renderManifest(
    content: string,
    namespace: string,
    built: Map<string, string>,
    defaultImage: string,
  ): string {
    const docs = content.split(/^---\s*$/m);
    const kept: string[] = [];
    for (const doc of docs) {
      if (!doc.trim()) continue;
      if (/\bkind:\s*Namespace\b/.test(doc)) continue; // created separately
      // Choose the image for this doc: a built app image matched to the Deployment, else
      // the default placeholder. Only affects the REPLACE_WITH_IMAGE placeholder.
      const image = this.imageForDoc(doc, built, defaultImage);
      let d = doc
        .replace(/REPLACE_WITH_IMAGE:latest/g, image)
        .replace(/REPLACE_WITH_IMAGE/g, image)
        // Rewrite any metadata namespace to the target so the object matches `-n <ns>`.
        .replace(/(\n[ \t]*namespace:[ \t]*)[^\n]+/g, `$1${namespace}`);
      // A `:latest` image defaults to imagePullPolicy=Always, so kubelet tries a remote
      // pull and ImagePullBackOffs on a locally-built/kind-loaded image. Force
      // IfNotPresent (safe for pushed images too) unless the manifest already sets it.
      if (/\bimage:/.test(d) && !/imagePullPolicy:/.test(d)) {
        d = d.replace(
          /(\n([ \t]*)image:[ \t]*\S+)/g,
          `$1\n$2imagePullPolicy: IfNotPresent`,
        );
      }
      kept.push(d.trim());
    }
    return kept.join('\n---\n') + (kept.length ? '\n' : '');
  }

  /** Pick the built image for a manifest doc (match Deployment name to a build key). */
  private imageForDoc(doc: string, built: Map<string, string>, defaultImage: string): string {
    if (built.size === 0) return defaultImage;
    if (built.size === 1) return [...built.values()][0];
    const nameMatch = doc.match(/\bkind:\s*Deployment\b[\s\S]*?name:\s*([A-Za-z0-9-]+)/);
    const depName = (nameMatch?.[1] || '').toLowerCase();
    if (depName) {
      for (const [key, tag] of built) {
        if (depName.includes(key) || key.includes(depName)) return tag;
      }
    }
    // Fall back to the root/app build if present, else any build.
    return built.get('app') || [...built.values()][0];
  }

  /**
   * Ensure the app source is available for building. Linked workspaces persist only the
   * scaffolded infra/ (artifact_mode=iac_only), so there is no source to build from. When the
   * source is absent but the wizard config carries the repo URL, clone it (shallow) into a
   * sibling .launchpad-src dir and return that as the build root. Returns the original root
   * when the source is already present or no repo URL is known.
   */
  private async ensureSourceCheckout(
    root: string,
    cloud: Record<string, any> | null,
    ownerId: string | null,
  ): Promise<string> {
    const services: any[] = Array.isArray(cloud?.detection?.services) ? cloud!.detection.services : [];
    const hasSource =
      services.some((s) => s?.path && fs.existsSync(path.join(root, String(s.path)))) ||
      ['apps', 'packages', 'Dockerfile', 'package.json', 'go.mod', 'requirements.txt', 'pyproject.toml'].some(
        (m) => fs.existsSync(path.join(root, m)),
      );
    if (hasSource) return root;

    const repoUrl = String(cloud?.git_repo_url || '').trim();
    if (!repoUrl) return root;
    const branch = String(cloud?.git_branch || 'main').trim() || 'main';
    const commit = String(cloud?.commit_sha || '').trim();
    const srcDir = path.join(root, '.launchpad-src');
    // Reuse an existing checkout (repeat provisions of the same env).
    if (fs.existsSync(path.join(srcDir, '.git'))) return srcDir;

    // Resolve a clone token for private repos (undefined for public ones).
    let token: string | undefined;
    try {
      token = await this.githubApp.resolveCloneToken({ repoUrl });
    } catch {
      token = undefined;
    }
    let authUrl = repoUrl;
    if (token && /^https?:\/\//.test(repoUrl)) {
      const u = new URL(repoUrl);
      u.username = 'x-access-token';
      u.password = token;
      authUrl = u.toString();
    }

    fs.rmSync(srcDir, { recursive: true, force: true });
    fs.mkdirSync(srcDir, { recursive: true });
    this.logger.log(`real_k8s_checkout repo=${repoUrl} branch=${branch} -> ${srcDir}`);
    // GIT_TERMINAL_PROMPT=0 so a private repo without a token fails fast instead of hanging.
    const gitEnv = { GIT_TERMINAL_PROMPT: '0' };
    const cloned = await this.exec(
      'git',
      ['clone', '--depth', '1', '--branch', branch, authUrl, srcDir],
      180000,
      true,
      gitEnv,
    );
    if (!cloned) {
      // Fall back to a default-branch shallow clone (the branch name may not exist remotely).
      await this.exec('git', ['clone', '--depth', '1', authUrl, srcDir], 180000, true, gitEnv);
    }
    // Best-effort checkout of the pinned commit (shallow clones may not contain it).
    if (commit && fs.existsSync(path.join(srcDir, '.git'))) {
      const fetched = await this.exec(
        'git',
        ['-C', srcDir, 'fetch', '--depth', '1', 'origin', commit],
        120000,
        true,
        gitEnv,
      );
      if (fetched) await this.exec('git', ['-C', srcDir, 'checkout', commit], 60000, true, gitEnv);
    }
    return fs.existsSync(path.join(srcDir, '.git')) ? srcDir : root;
  }

  /**
   * Inject PORT=<containerPort> into every Deployment's first container so PORT-honoring
   * runtimes bind the (possibly scaffold-remapped) port the Service targetPort and readiness
   * probe expect. Best-effort and idempotent; never throws.
   */
  private async injectPortEnv(
    context: string,
    namespace: string,
    runEnv: Record<string, string> = {},
  ): Promise<void> {
    try {
      const { stdout } = await execFileAsync(
        'kubectl',
        [
          '--context',
          context,
          '-n',
          namespace,
          'get',
          'deploy',
          '-o',
          'jsonpath={range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].name}{" "}{.spec.template.spec.containers[0].ports[0].containerPort}{"\\n"}{end}',
        ],
        { timeout: 15000, env: { ...process.env, ...runEnv } },
      );
      for (const line of stdout.split('\n')) {
        const [name, container, port] = line.trim().split(/\s+/);
        if (!name || !container || !port) continue;
        await this.kubectl(
          context,
          ['-n', namespace, 'set', 'env', `deployment/${name}`, '-c', container, `PORT=${port}`],
          { ignoreError: true, env: runEnv },
        );
      }
    } catch {
      // best-effort
    }
  }

  /**
   * Phase 2: build a container image per Dockerfile in the workspace and stage it where
   * the target cluster can pull it. Returns a map of build-key -> image ref. Best-effort:
   * returns empty when docker is unavailable, no Dockerfile exists, or a cloud registry
   * is not configured, so the caller falls back to the placeholder image.
   */
  private async buildAndStageImages(
    root: string,
    context: string,
    provider: string | null | undefined,
    envId: string,
    runEnv: Record<string, string> = {},
  ): Promise<Map<string, string>> {
    const map = new Map<string, string>();
    if (!(await this.commandExists('docker'))) return map;

    // Discover (key, contextDir, dockerfile) builds. Prefer a repo Dockerfile; generate a
    // stack-appropriate one when missing (Node/Python/Go). Root context first (single-repo),
    // then apps/*, packages/* (multi-repo). Dirs with no recognizable app yield no build.
    const builds: Array<{ key: string; dir: string; dockerfile: string }> = [];
    const rootDf = this.ensureDockerfile(root);
    if (rootDf) builds.push({ key: 'app', dir: root, dockerfile: rootDf });
    for (const parent of ['apps', 'packages']) {
      const pdir = path.join(root, parent);
      if (fs.existsSync(pdir) && fs.statSync(pdir).isDirectory()) {
        for (const sub of fs.readdirSync(pdir)) {
          const subdir = path.join(pdir, sub);
          if (!fs.statSync(subdir).isDirectory()) continue;
          const df = this.ensureDockerfile(subdir);
          if (df) builds.push({ key: this.slug(sub), dir: subdir, dockerfile: df });
        }
      }
    }
    if (builds.length === 0) return map;

    const isCloud = Boolean(provider && provider !== 'local');
    const registry = isCloud ? this.cloudRegistry(provider) : '';
    if (isCloud && !registry) {
      this.logger.warn(
        'real_k8s_phase2_skipped: cloud provider but no container registry configured - set ' +
          'GAR_REGISTRY (gcp) / ECR_REGISTRY (aws) / ACR_REGISTRY (azure) or CONTAINER_REGISTRY',
      );
      return map;
    }

    for (const b of builds) {
      const shortId = envId.replace(/-/g, '').slice(0, 8);
      const tag = registry
        ? `${registry.replace(/\/+$/, '')}/lp-${shortId}-${b.key}:latest`
        : `launchpad/lp-${shortId}-${b.key}:latest`;
      this.logger.log(`real_k8s_build image=${tag} dockerfile=${b.dockerfile}`);
      await this.exec('docker', ['build', '-t', tag, '-f', b.dockerfile, b.dir], 600000);

      if (registry) {
        // Cloud: authenticate the docker CLI to the provider's registry, then push.
        await this.pushToCloudRegistry(provider!, registry, tag, runEnv);
      } else {
        // Local: load into the kind/k3d cluster so pods pull without a registry.
        await this.stageToLocalCluster(context, tag);
      }
      map.set(b.key, tag);
    }
    return map;
  }

  /**
   * Resolve the container registry prefix for a cloud provider from config. Each cloud has
   * its own env (GAR/ECR/ACR), with CONTAINER_REGISTRY as a generic fallback. Examples:
   *   gcp:   europe-west3-docker.pkg.dev/<project>/<repo>
   *   aws:   <account>.dkr.ecr.<region>.amazonaws.com/<repo>
   *   azure: <name>.azurecr.io/<repo>
   */
  private cloudRegistry(provider: string | null | undefined): string {
    const p = (provider || '').toLowerCase();
    const key = { gcp: 'GAR_REGISTRY', aws: 'ECR_REGISTRY', azure: 'ACR_REGISTRY' }[p];
    return (key ? this.cfg(key) : '') || this.cfg('CONTAINER_REGISTRY');
  }

  /**
   * Authenticate docker to the cloud registry (provider-specific), ensure the repo exists
   * where required (ECR), and push. Auth commands: gcp `gcloud auth configure-docker`,
   * aws `aws ecr get-login-password | docker login`, azure `az acr login`.
   */
  private async pushToCloudRegistry(
    provider: string,
    registry: string,
    tag: string,
    env: Record<string, string> = {},
  ): Promise<void> {
    const host = registry.split('/')[0];
    const p = provider.toLowerCase();
    if (p === 'gcp') {
      // Uses the isolated CLOUDSDK_CONFIG (SA-activated) from `env` so the push authenticates
      // as the workspace's service account, not the host's gcloud login.
      await this.exec('gcloud', ['auth', 'configure-docker', host, '--quiet'], 60000, true, env);
    } else if (p === 'aws') {
      const region =
        (env.AWS_REGION || '').trim() ||
        this.cfg('AWS_REGION') ||
        host.match(/\.ecr\.([^.]+)\.amazonaws/)?.[1] ||
        'us-east-1';
      // ECR requires the repository to exist before push; create it best-effort.
      const repoPath = tag.slice(host.length + 1).split(':')[0];
      await this.exec(
        'sh',
        [
          '-c',
          `aws ecr describe-repositories --region ${region} --repository-names "${repoPath}" >/dev/null 2>&1 || ` +
            `aws ecr create-repository --region ${region} --repository-name "${repoPath}" >/dev/null 2>&1`,
        ],
        60000,
        true,
        env,
      );
      await this.exec(
        'sh',
        [
          '-c',
          `aws ecr get-login-password --region ${region} | docker login --username AWS --password-stdin ${host}`,
        ],
        60000,
        true,
        env,
      );
    } else if (p === 'azure') {
      const acrName = host.split('.')[0];
      await this.exec('az', ['acr', 'login', '--name', acrName], 60000, true, env);
    }
    await this.exec('docker', ['push', tag], 600000);
  }

  /**
   * Return a Dockerfile path for a build context: the repo's own Dockerfile if present,
   * else a generated stack-appropriate one (written as Dockerfile.launchpad). Returns null
   * when the directory has no recognizable app (so no build is attempted). Mirrors the
   * intent of FastAPI's dockerfile_scaffold (kept minimal here).
   */
  private ensureDockerfile(dir: string): string | null {
    const existing = path.join(dir, 'Dockerfile');
    if (fs.existsSync(existing)) return existing;
    const has = (f: string) => fs.existsSync(path.join(dir, f));
    let content: string | null = null;
    if (has('package.json')) {
      content = [
        'FROM node:20-alpine',
        'WORKDIR /app',
        'COPY package*.json ./',
        'RUN npm install --omit=dev || npm install',
        'COPY . .',
        'ENV PORT=8080',
        'EXPOSE 8080',
        'CMD ["npm","start"]',
        '',
      ].join('\n');
    } else if (has('requirements.txt')) {
      content = [
        'FROM python:3.12-slim',
        'WORKDIR /app',
        'COPY requirements.txt ./',
        'RUN pip install --no-cache-dir -r requirements.txt',
        'COPY . .',
        'ENV PORT=8080',
        'EXPOSE 8080',
        'CMD ["sh","-c","python app.py || python main.py || python server.py"]',
        '',
      ].join('\n');
    } else if (has('go.mod')) {
      content = [
        'FROM golang:1.22-alpine AS build',
        'WORKDIR /src',
        'COPY . .',
        'RUN go build -o /app ./...',
        'FROM alpine:3.20',
        'COPY --from=build /app /app',
        'ENV PORT=8080',
        'EXPOSE 8080',
        'CMD ["/app"]',
        '',
      ].join('\n');
    }
    if (!content) return null;
    const generated = path.join(dir, 'Dockerfile.launchpad');
    try {
      fs.writeFileSync(generated, content, 'utf-8');
    } catch {
      return null;
    }
    return generated;
  }

  /** Load an image into the local cluster (kind or k3d) named by the kubeconfig context. */
  private async stageToLocalCluster(context: string, tag: string): Promise<void> {
    if (context.startsWith('kind-')) {
      await this.exec('kind', ['load', 'docker-image', tag, '--name', context.slice(5)], 600000);
    } else if (context.startsWith('k3d-')) {
      await this.exec('k3d', ['image', 'import', tag, '-c', context.slice(4)], 600000);
    } else {
      // Unknown local cluster type: try kind then k3d, best-effort.
      const cluster = context.replace(/^[^-]*-/, '');
      const kindOk = await this.exec('kind', ['load', 'docker-image', tag, '--name', cluster], 600000, true);
      if (!kindOk) await this.exec('k3d', ['image', 'import', tag, '-c', cluster], 600000, true);
    }
  }

  private async commandExists(cmd: string): Promise<boolean> {
    try {
      await execFileAsync('command', ['-v', cmd], { timeout: 5000, shell: '/bin/sh' as any });
      return true;
    } catch {
      try {
        await execFileAsync(cmd, ['--version'], { timeout: 5000 });
        return true;
      } catch {
        return false;
      }
    }
  }

  /** Run a command with a timeout; returns true on success. Throws on failure unless soft. */
  private async exec(
    cmd: string,
    args: string[],
    timeout: number,
    soft = false,
    env: Record<string, string> = {},
  ): Promise<boolean> {
    try {
      await execFileAsync(cmd, args, { timeout, env: { ...process.env, ...env } });
      return true;
    } catch (err: any) {
      const msg = (err?.stderr || err?.message || String(err)).slice(0, 600);
      if (soft) {
        this.logger.debug(`exec (soft) ${cmd} ${args.join(' ')}: ${msg}`);
        return false;
      }
      throw new Error(`${cmd} ${args.slice(0, 2).join(' ')} failed: ${msg}`);
    }
  }

  /** Read the ingress manifest's backend service name (the preview target), if any. */
  private previewServiceName(applyDir: string): string | null {
    try {
      const dirFiles = fs.readdirSync(applyDir);
      // The Ingress backend is the AUTHORITATIVE preview target (the scaffold points it at the
      // is_preview_target service). Scan every file for a kind: Ingress and read its backend
      // service name - the apply dir renames files with a NN- prefix, so never match by an
      // exact `ingress.yaml` filename.
      for (const f of dirFiles) {
        if (!/\.ya?ml$/i.test(f)) continue;
        const c = fs.readFileSync(path.join(applyDir, f), 'utf-8');
        if (!/kind:\s*Ingress\b/.test(c)) continue;
        const m = c.match(/service:\s*\n\s*name:\s*([A-Za-z0-9-]+)/);
        if (m) return m[1];
      }
      // Fallback: first *-service.yaml file's metadata name.
      for (const f of dirFiles) {
        if (f.endsWith('-service.yaml')) {
          const c = fs.readFileSync(path.join(applyDir, f), 'utf-8');
          const m = c.match(/kind:\s*Service[\s\S]*?name:\s*([A-Za-z0-9-]+)/);
          if (m) return m[1];
        }
      }
    } catch {
      // ignore
    }
    return null;
  }

  /** Poll (bounded) for a Service's LoadBalancer external IP/hostname to be assigned. */
  private async waitForLoadBalancerIp(
    context: string,
    namespace: string,
    service: string,
    env: Record<string, string> = {},
  ): Promise<string | null> {
    for (let i = 0; i < 24; i++) {
      try {
        const { stdout } = await execFileAsync(
          'kubectl',
          [
            '--context',
            context,
            '-n',
            namespace,
            'get',
            'svc',
            service,
            '-o',
            'jsonpath={.status.loadBalancer.ingress[0].ip}{" "}{.status.loadBalancer.ingress[0].hostname}',
          ],
          { timeout: 15000, env: { ...process.env, ...env } },
        );
        const [ip, hostname] = stdout.trim().split(/\s+/);
        const host = (ip || hostname || '').trim();
        if (host) return host;
      } catch {
        // keep polling
      }
      await new Promise((r) => setTimeout(r, 5000));
    }
    return null;
  }

  /** Deployment names whose available replicas are below desired (i.e. not yet Ready). */
  private async notReadyDeployments(
    context: string,
    namespace: string,
    env: Record<string, string> = {},
  ): Promise<string[]> {
    try {
      const { stdout } = await execFileAsync(
        'kubectl',
        [
          '--context',
          context,
          '-n',
          namespace,
          'get',
          'deployments',
          '-o',
          'jsonpath={range .items[*]}{.metadata.name}{" "}{.status.availableReplicas}{" "}{.spec.replicas}{"\\n"}{end}',
        ],
        { timeout: 15000, env: { ...process.env, ...env } },
      );
      const notReady: string[] = [];
      for (const line of stdout.split('\n')) {
        const [name, available, desired] = line.trim().split(/\s+/);
        if (!name) continue;
        const want = Number.parseInt(desired || '1', 10) || 1;
        const have = Number.parseInt(available || '0', 10) || 0;
        if (have < want) notReady.push(name);
      }
      return notReady;
    } catch {
      // If we can't read status, treat as not-ready so the loop keeps waiting (until timeout).
      return ['(status-unavailable)'];
    }
  }

  private async listDeployments(
    context: string,
    namespace: string,
    env: Record<string, string> = {},
  ): Promise<string[]> {
    try {
      const { stdout } = await execFileAsync(
        'kubectl',
        [
          '--context',
          context,
          '-n',
          namespace,
          'get',
          'deployments',
          '-o',
          'jsonpath={.items[*].metadata.name}',
        ],
        { timeout: 15000, env: { ...process.env, ...env } },
      );
      return stdout.trim().split(/\s+/).filter(Boolean);
    } catch {
      return [];
    }
  }

  /**
   * Resolve the real preview URL: a Service LoadBalancer ingress IP/hostname if present,
   * else the wildcard ingress host (ws-{id}.{PREVIEW_BASE_DOMAIN}) when a base domain is
   * configured, else null.
   */
  private async resolveUrl(
    env: { id: string; provider?: string | null },
    context: string,
    namespace: string,
    runEnv: Record<string, string> = {},
    localNodePort: number | null = null,
  ): Promise<string | null> {
    // Real cloud endpoint: a Service LoadBalancer external IP/hostname (GKE/EKS/AKS give a
    // real IP). This is THE cloud preview URL.
    try {
      const { stdout } = await execFileAsync(
        'kubectl',
        [
          '--context',
          context,
          '-n',
          namespace,
          'get',
          'svc',
          '-o',
          'jsonpath={range .items[*]}{.status.loadBalancer.ingress[0].ip}{" "}{.status.loadBalancer.ingress[0].hostname}{"\\n"}{end}',
        ],
        { timeout: 15000, env: { ...process.env, ...runEnv } },
      );
      for (const line of stdout.split('\n')) {
        const [ip, hostname] = line.trim().split(/\s+/);
        const host = (hostname || ip || '').trim();
        if (host) return `http://${host}`;
      }
    } catch {
      // fall through
    }
    const isCloud = Boolean(env.provider && env.provider !== 'local');
    // The ws-{id}.{base} wildcard is the PROD-LOCAL homelab ingress only - never use it for
    // a cloud deploy. Cloud without a LoadBalancer IP yet -> null (no fake homelab URL).
    if (isCloud) return null;

    // Local dev with the Cloudflare tunnel active (homelab / ENVIRONMENT=production): the
    // wildcard ingress host is reachable. Otherwise (plain dev box) it is NOT - fall back to
    // the localhost NodePort we exposed so the URL is actually reachable on this machine.
    if (this.previewTunnelActive()) {
      const base = this.cfg('PREVIEW_BASE_DOMAIN');
      if (base) return `https://ws-${env.id.slice(0, 8)}.${base}`;
    }
    if (localNodePort) return `http://localhost:${localNodePort}`;
    return null;
  }

  private async kubectl(
    context: string,
    args: string[],
    opts: { ignoreError?: boolean; env?: Record<string, string> } = {},
  ): Promise<string> {
    try {
      const { stdout } = await execFileAsync('kubectl', ['--context', context, ...args], {
        timeout: 200000,
        env: { ...process.env, ...(opts.env ?? {}) },
      });
      return stdout;
    } catch (err: any) {
      const raw = ((err?.stderr || '') + ' ' + (err?.message || '')).trim();
      const unreachable =
        err?.killed ||
        err?.signal === 'SIGTERM' ||
        err?.code === 'ETIMEDOUT' ||
        /context deadline exceeded|Unable to connect to the server|i\/o timeout|dial tcp|EOF|TLS handshake timeout/i.test(
          raw,
        );
      const msg = unreachable
        ? `cluster '${context}' is unreachable (connection timed out). Verify the API server is reachable ` +
          `from the worker host - for GKE add this host's egress IP to the cluster's control-plane ` +
          `"authorized networks", or run the worker inside the cluster network. [${(raw || 'timed out').slice(0, 160)}]`
        : (raw || String(err)).slice(0, 500);
      if (opts.ignoreError) {
        this.logger.debug(`kubectl (ignored) ${args.join(' ')}: ${msg}`);
        return '';
      }
      throw new Error(`kubectl ${args.join(' ')} failed: ${msg}`);
    }
  }
}
