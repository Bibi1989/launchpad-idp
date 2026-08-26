import type { CloudServiceGroup } from '../cloud-providers.types';

/**
 * Per-cloud services grouped by how the app runs (kubernetes / docker / vm / paas).
 * Ported from app/providers/provider_services.py.
 */
const K8S = 'Kubernetes';
const DOCKER = 'Docker / Docker Compose';
const VM = 'Virtual Machine';
const PAAS = 'Managed Platform';

const SERVICE_CATALOG: Record<string, CloudServiceGroup[]> = {
  aws: [
    group('kubernetes', K8S, [['eks', 'Amazon EKS', 'Managed Kubernetes control plane.']]),
    group('docker', DOCKER, [
      ['ecs-fargate', 'ECS on Fargate', 'Serverless containers, no VM to manage.'],
      ['ec2-docker', 'EC2 + Docker', 'Docker Engine on an EC2 instance (cloud-init).'],
    ]),
    group('vm', VM, [['ec2', 'Amazon EC2', 'Raw Linux VM bootstrapped via cloud-init.']]),
  ],
  gcp: [
    group('kubernetes', K8S, [['gke', 'Google GKE', 'Managed Kubernetes on Google Cloud.']]),
    group('docker', DOCKER, [
      ['cloud-run', 'Cloud Run', 'Fully managed container runtime.'],
      ['gce-docker', 'GCE + Docker', 'Docker Engine on a Compute Engine VM (cloud-init).'],
    ]),
    group('vm', VM, [['gce', 'Compute Engine', 'Raw Linux VM bootstrapped via cloud-init.']]),
  ],
  azure: [
    group('kubernetes', K8S, [['aks', 'Azure AKS', 'Managed Kubernetes on Azure.']]),
    group('docker', DOCKER, [
      ['container-apps', 'Container Apps', 'Serverless containers on Azure.'],
      ['aci', 'Container Instances', 'Single-container serverless runtime.'],
      ['vm-docker', 'Azure VM + Docker', 'Docker Engine on an Azure VM (cloud-init).'],
    ]),
    group('vm', VM, [['azure-vm', 'Azure VM', 'Raw Linux VM bootstrapped via cloud-init.']]),
  ],
  hetzner: [
    group('kubernetes', K8S, [['k3s', 'k3s on Cloud Server', 'Lightweight Kubernetes on a Hetzner VM.']]),
    group('docker', DOCKER, [['server-docker', 'Cloud Server + Docker', 'Docker Engine on a Hetzner VM (cloud-init).']]),
    group('vm', VM, [['cloud-server', 'Cloud Server', 'Raw Linux VM bootstrapped via cloud-init.']]),
  ],
  digitalocean: [
    group('kubernetes', K8S, [['doks', 'DigitalOcean Kubernetes', 'Managed Kubernetes (DOKS).']]),
    group('docker', DOCKER, [
      ['app-platform', 'App Platform', 'Managed container/app platform.'],
      ['droplet-docker', 'Droplet + Docker', 'Docker Engine on a Droplet (cloud-init).'],
    ]),
    group('vm', VM, [['droplet', 'Droplet', 'Raw Linux VM bootstrapped via cloud-init.']]),
  ],
  linode: [
    group('kubernetes', K8S, [['lke', 'Linode Kubernetes Engine', 'Managed Kubernetes (LKE).']]),
    group('docker', DOCKER, [['linode-docker', 'Linode + Docker', 'Docker Engine on a Linode instance (cloud-init).']]),
    group('vm', VM, [['linode-instance', 'Linode Instance', 'Raw Linux VM bootstrapped via cloud-init.']]),
  ],
  railway: [
    group('paas', PAAS, [['railway-service', 'Railway Service', 'Deploy a container or repo as a service.']]),
  ],
  render: [
    group('paas', PAAS, [
      ['render-web', 'Render Web Service', 'Deploy a container image or git repo.'],
      ['render-worker', 'Render Background Worker', 'Long-running worker service.'],
    ]),
  ],
  cloudflare: [
    group('paas', PAAS, [
      ['workers', 'Cloudflare Workers', 'Edge JavaScript runtime.'],
      ['pages', 'Cloudflare Pages', 'Static / SSR site hosting.'],
    ]),
  ],
};

const LEGACY_ALIAS: Record<string, string> = {
  'aws-legacy': 'aws',
  'gcp-legacy': 'gcp',
  'azure-legacy': 'azure',
};

export { SERVICE_CATALOG };

export function servicesFor(providerId: string): CloudServiceGroup[] {
  const key = LEGACY_ALIAS[providerId] ?? providerId;
  return SERVICE_CATALOG[key] ?? [];
}

function group(
  runtime: string,
  label: string,
  services: Array<[string, string, string]>,
): CloudServiceGroup {
  return {
    runtime,
    label,
    services: services.map(([id, svcLabel, description]) => ({ id, label: svcLabel, description })),
  };
}
