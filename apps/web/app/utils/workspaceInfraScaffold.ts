import type {
  CicdPlatform,
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

export function defaultInfraGenerationConfig(
  opts: { isLocal?: boolean } = {},
): InfraGenerationConfig {
  if (opts.isLocal) {
    return {
      provision: { enabled: false, engine: 'terraform' },
      kubernetes: { enabled: true, mode: 'k8s' },
      cicd: { enabled: false, platform: 'github', security: defaultCicdSecurityConfig() },
    }
  }
  return {
    provision: { enabled: true, engine: 'terraform' },
    kubernetes: { enabled: false, mode: 'k8s' },
    cicd: { enabled: false, platform: 'github', security: defaultCicdSecurityConfig() },
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
  return config.kubernetes.mode === 'helm' ? 'helm' : 'raw_manifests'
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
      mode: packaging === 'helm' ? 'helm' : 'k8s',
    },
    cicd: { enabled: false, platform: cicdPlatform, security: defaultCicdSecurityConfig() },
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

export function buildCiCdScaffold(
  platform: CicdPlatform,
  security: CicdSecurityConfig = defaultCicdSecurityConfig(),
): ScaffoldTarget[] {
  const content = renderCicdWorkflow(platform, security)
  if (platform === 'github') {
    return [
      {
        path: 'ci/github/workflows/deploy.yml',
        content,
      },
    ]
  }
  return [
    {
      path: 'ci/gitlab/.gitlab-ci.yml',
      content,
    },
  ]
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
  return ['helm upgrade --install app-chart infra/helm/app-chart/']
}
