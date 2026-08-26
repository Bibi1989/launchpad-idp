import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { promisify } from 'node:util';

import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

const execFileAsync = promisify(execFile);

export interface CloudKubeTarget {
  context: string;
  /** Extra env vars to apply to EVERY kubectl/cloud command for this deploy (isolated auth). */
  env: Record<string, string>;
  cleanup: () => void;
}

/**
 * Acquire a kubeconfig context for a cloud Kubernetes cluster (GKE/EKS/AKS) from the user's
 * STORED cloud credentials, mirroring FastAPI's cloud_kubernetes.py: authenticate the CLI
 * with the vault creds and run the provider's get-credentials, into an ISOLATED kubeconfig +
 * CLI config dir so nothing leaks into the host's ~/.kube or ~/.config. This is what lets a
 * cloud deploy work without a pre-baked kubeconfig context (the NestJS parity gap with FastAPI).
 *
 * Covers the common credential modes: GCP service-account key JSON, AWS access keys, Azure
 * service principal. (FastAPI additionally supports GCP Workload Identity Federation /
 * external_account - not ported here.)
 */
@Injectable()
export class CloudKubeconfigService {
  private readonly logger = new Logger(CloudKubeconfigService.name);

  constructor(private readonly config: ConfigService) {}

  private cfg(key: string): string {
    return (this.config.get<string>(key) ?? '').trim();
  }

  /** Returns a ready-to-use context + env, or null when the provider/creds are unsupported. */
  async acquire(
    provider: string,
    creds: Record<string, string>,
    cloud: Record<string, any> | null,
    envId: string,
  ): Promise<CloudKubeTarget | null> {
    const p = (provider || '').toLowerCase();
    const workDir = fs.mkdtempSync(path.join(os.tmpdir(), `lp-kube-${envId.slice(0, 8)}-`));
    const kubeconfig = path.join(workDir, 'kubeconfig');
    const cleanup = () => {
      try {
        fs.rmSync(workDir, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
    };
    try {
      if (p === 'gcp') return await this.acquireGke(creds, cloud, workDir, kubeconfig, cleanup);
      if (p === 'aws') return await this.acquireEks(creds, cloud, kubeconfig, cleanup);
      if (p === 'azure') return await this.acquireAks(creds, cloud, kubeconfig, cleanup);
      cleanup();
      return null;
    } catch (err) {
      cleanup();
      throw err;
    }
  }

  private resource(cloud: Record<string, any> | null, key: string): string {
    const res = (cloud?.cloud?.resources ?? cloud?.resources ?? {}) as Record<string, any>;
    return String(res?.[key] ?? '').trim();
  }

  // ----- GCP / GKE -----
  private async acquireGke(
    creds: Record<string, string>,
    cloud: Record<string, any> | null,
    workDir: string,
    kubeconfig: string,
    cleanup: () => void,
  ): Promise<CloudKubeTarget> {
    const saKey = (creds.gcp_sa_key_json || '').trim();
    const project = (creds.gcp_project_id || this.resource(cloud, 'project_id') || '').trim();
    const location =
      creds.gcp_region || this.resource(cloud, 'region') || this.cfg('GKE_PREVIEW_REGION');
    const cluster =
      this.resource(cloud, 'gke_cluster_name') ||
      this.resource(cloud, 'cluster_name') ||
      this.cfg('GKE_PREVIEW_CLUSTER') ||
      'launchpad-previews';
    if (!saKey || !project || !location) {
      cleanup();
      throw new Error(
        'GKE credentials incomplete: need gcp_sa_key_json + gcp_project_id + a region ' +
          '(gcp_region / cloud.resources.region / GKE_PREVIEW_REGION).',
      );
    }
    // Isolated gcloud config so the activated SA never touches ~/.config/gcloud.
    const cloudsdkConfig = path.join(workDir, 'gcloud');
    fs.mkdirSync(cloudsdkConfig, { recursive: true });
    const keyFile = path.join(workDir, 'gcp-sa.json');
    fs.writeFileSync(keyFile, saKey, { mode: 0o600 });
    const env: Record<string, string> = {
      CLOUDSDK_CONFIG: cloudsdkConfig,
      KUBECONFIG: kubeconfig,
      CLOUDSDK_CORE_PROJECT: project,
      GOOGLE_CLOUD_PROJECT: project,
      // gke-gcloud-auth-plugin (used by kubectl for GKE) reads gcloud config; keep it isolated.
      USE_GKE_GCLOUD_AUTH_PLUGIN: 'True',
    };

    await this.run('gcloud', ['auth', 'activate-service-account', `--key-file=${keyFile}`], env, 60000);

    const locFlag = this.isGkeZone(location) ? '--zone' : '--region';
    // The GKE DNS-based control-plane endpoint is not IP-firewalled by authorized-networks,
    // so it is reachable where the public IP endpoint is blocked. Default ON; opt out with
    // GKE_DNS_ENDPOINT=false (e.g. clusters without the DNS endpoint enabled).
    const dnsRaw = this.cfg('GKE_DNS_ENDPOINT').toLowerCase();
    const dnsEndpoint = dnsRaw === '' ? true : ['1', 'true', 'yes', 'on'].includes(dnsRaw);
    const args = [
      'container',
      'clusters',
      'get-credentials',
      cluster,
      locFlag,
      location,
      `--project=${project}`,
    ];
    if (dnsEndpoint) args.push('--dns-endpoint');
    await this.run('gcloud', args, env, 90000);

    const context = `gke_${project}_${location}_${cluster}`;
    this.logger.log(`cloud_kubeconfig gke context=${context} project=${project} cluster=${cluster}`);
    return { context, env, cleanup };
  }

  private isGkeZone(location: string): boolean {
    // Zones look like europe-west3-a (region + "-<letter>"); regions like europe-west3.
    return /-[a-z]$/.test(location);
  }

  // ----- AWS / EKS -----
  private async acquireEks(
    creds: Record<string, string>,
    cloud: Record<string, any> | null,
    kubeconfig: string,
    cleanup: () => void,
  ): Promise<CloudKubeTarget> {
    const accessKey = (creds.aws_access_key_id || '').trim();
    const secretKey = (creds.aws_secret_access_key || '').trim();
    const region = creds.aws_region || this.resource(cloud, 'region') || this.cfg('EKS_PREVIEW_REGION');
    const cluster =
      this.resource(cloud, 'eks_cluster_name') ||
      this.resource(cloud, 'cluster_name') ||
      this.cfg('EKS_PREVIEW_CLUSTER') ||
      'launchpad-previews';
    if (!accessKey || !secretKey || !region) {
      cleanup();
      throw new Error('EKS credentials incomplete: need aws_access_key_id + aws_secret_access_key + region.');
    }
    const env: Record<string, string> = {
      KUBECONFIG: kubeconfig,
      AWS_ACCESS_KEY_ID: accessKey,
      AWS_SECRET_ACCESS_KEY: secretKey,
      AWS_DEFAULT_REGION: region,
      AWS_REGION: region,
    };
    if (creds.aws_session_token) env.AWS_SESSION_TOKEN = creds.aws_session_token.trim();
    await this.run(
      'aws',
      ['eks', 'update-kubeconfig', '--name', cluster, '--region', region, '--kubeconfig', kubeconfig],
      env,
      90000,
    );
    // aws names the context by the cluster ARN; read it back from the kubeconfig.
    const context = this.currentContextFrom(kubeconfig) || cluster;
    this.logger.log(`cloud_kubeconfig eks context=${context} region=${region} cluster=${cluster}`);
    return { context, env, cleanup };
  }

  // ----- Azure / AKS -----
  private async acquireAks(
    creds: Record<string, string>,
    cloud: Record<string, any> | null,
    kubeconfig: string,
    cleanup: () => void,
  ): Promise<CloudKubeTarget> {
    const tenant = (creds.azure_tenant_id || '').trim();
    const clientId = (creds.azure_client_id || '').trim();
    const clientSecret = (creds.azure_client_secret || '').trim();
    const rg = this.resource(cloud, 'resource_group') || this.cfg('AKS_PREVIEW_RESOURCE_GROUP');
    const cluster =
      this.resource(cloud, 'aks_cluster_name') ||
      this.resource(cloud, 'cluster_name') ||
      this.cfg('AKS_PREVIEW_CLUSTER') ||
      'launchpad-previews';
    if (!tenant || !clientId || !clientSecret || !rg) {
      cleanup();
      throw new Error(
        'AKS credentials incomplete: need azure_tenant_id + azure_client_id + azure_client_secret + a resource group.',
      );
    }
    // Isolated az config dir so the service-principal login does not touch ~/.azure.
    const azConfig = path.join(path.dirname(kubeconfig), 'az');
    fs.mkdirSync(azConfig, { recursive: true });
    const env: Record<string, string> = { KUBECONFIG: kubeconfig, AZURE_CONFIG_DIR: azConfig };
    await this.run(
      'az',
      ['login', '--service-principal', '-u', clientId, '-p', clientSecret, '--tenant', tenant],
      env,
      90000,
    );
    if (creds.azure_subscription_id) {
      await this.run('az', ['account', 'set', '--subscription', creds.azure_subscription_id.trim()], env, 30000);
    }
    await this.run(
      'az',
      ['aks', 'get-credentials', '--resource-group', rg, '--name', cluster, '--file', kubeconfig, '--overwrite-existing'],
      env,
      90000,
    );
    const context = this.currentContextFrom(kubeconfig) || cluster;
    this.logger.log(`cloud_kubeconfig aks context=${context} rg=${rg} cluster=${cluster}`);
    return { context, env, cleanup };
  }

  private currentContextFrom(kubeconfig: string): string | null {
    try {
      const text = fs.readFileSync(kubeconfig, 'utf-8');
      const m = text.match(/current-context:\s*(\S+)/);
      return m ? m[1].replace(/^["']|["']$/g, '') : null;
    } catch {
      return null;
    }
  }

  private async run(
    cmd: string,
    args: string[],
    env: Record<string, string>,
    timeout: number,
  ): Promise<void> {
    try {
      await execFileAsync(cmd, args, { timeout, env: { ...process.env, ...env } });
    } catch (err: any) {
      const detail = (err?.stderr || err?.message || String(err)).slice(0, 400);
      throw new Error(`${cmd} ${args.slice(0, 3).join(' ')} failed: ${detail}`);
    }
  }
}
