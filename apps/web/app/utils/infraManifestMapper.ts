import type { CicdSecurityConfig, CicdPlatform } from '~/types/provisioning'
import {
  defaultCicdSecurityConfig,
  inferCicdSecurityFromContent,
  renderCicdWorkflow,
} from '~/utils/cicdWorkflowGenerator'

export interface KeyValueItem {
  key: string
  value: string
}

export type InfraManifestKind =
  | 'k8s-deployment'
  | 'k8s-service'
  | 'k8s-namespace'
  | 'k8s-hpa'
  | 'k8s-vpa'
  | 'k8s-pdb'
  | 'k8s-ingress'
  | 'k8s-configmap'
  | 'k8s-secret'
  | 'k8s-serviceaccount'
  | 'k8s-networkpolicy'
  | 'k8s-resourcequota'
  | 'k8s-limitrange'
  | 'helm-values'
  | 'terraform'
  | 'opentofu'
  | 'pulumi'
  | 'github-workflow'
  | 'gitlab-ci'
  | 'unknown'

export interface InfraManifestModel {
  kind: InfraManifestKind
  resourceName: string
  namespaceName: string
  appLabel: string
  replicas: number
  appImage: string
  imageTag: string
  pullPolicy: 'Always' | 'IfNotPresent' | 'Never'
  appPort: string
  targetPort: string
  /** Host node port — only for NodePort / LoadBalancer. Empty = allocate. */
  nodePort: string
  serviceType: 'ClusterIP' | 'NodePort' | 'LoadBalancer'
  cpuRequest: string
  cpuLimit: string
  memoryRequest: string
  memoryLimit: string
  envVars: KeyValueItem[]
  helmValueFile: string
  region: string
  instanceSize: string
  clusterSize: string
  resourceCount: number
  branch: string
  runner: string
  buildSecrets: KeyValueItem[]
  cicdSecurity: CicdSecurityConfig
  /** HPA */
  hpaMinReplicas: number
  hpaMaxReplicas: number
  hpaTargetCpu: number
  /** VPA */
  vpaUpdateMode: 'Off' | 'Initial' | 'Recreate' | 'Auto'
  /** PDB */
  pdbMinAvailable: string
  /** Ingress */
  ingressHost: string
  ingressClassName: string
  ingressPath: string
  /** ConfigMap / Secret data */
  dataEntries: KeyValueItem[]
  /** ResourceQuota hard limits */
  quotaCpuRequests: string
  quotaMemoryRequests: string
  quotaPods: string
  /** LimitRange defaults */
  limitDefaultCpu: string
  limitDefaultMemory: string
}

export const K8S_DEPLOYMENT_PATH = 'infra/k8s/manifests/deployment.yaml'
export const K8S_SERVICE_PATH = 'infra/k8s/manifests/service.yaml'

const DEFAULT_MODEL: InfraManifestModel = {
  kind: 'unknown',
  resourceName: '',
  namespaceName: '',
  appLabel: 'app',
  replicas: 1,
  appImage: 'nginx',
  imageTag: 'latest',
  pullPolicy: 'IfNotPresent',
  appPort: '80',
  targetPort: '80',
  nodePort: '',
  serviceType: 'ClusterIP',
  cpuRequest: '',
  cpuLimit: '',
  memoryRequest: '',
  memoryLimit: '',
  envVars: [],
  helmValueFile: 'values.yaml',
  region: '',
  instanceSize: '',
  clusterSize: '',
  resourceCount: 1,
  branch: 'main',
  runner: 'ubuntu-latest',
  buildSecrets: [],
  cicdSecurity: defaultCicdSecurityConfig(),
  hpaMinReplicas: 2,
  hpaMaxReplicas: 10,
  hpaTargetCpu: 70,
  vpaUpdateMode: 'Off',
  pdbMinAvailable: '1',
  ingressHost: '',
  ingressClassName: 'nginx',
  ingressPath: '/',
  dataEntries: [],
  quotaCpuRequests: '2',
  quotaMemoryRequests: '4Gi',
  quotaPods: '20',
  limitDefaultCpu: '100m',
  limitDefaultMemory: '128Mi',
}

function cloneDefault(): InfraManifestModel {
  const security = defaultCicdSecurityConfig()
  return {
    ...DEFAULT_MODEL,
    envVars: [],
    buildSecrets: [],
    dataEntries: [],
    cicdSecurity: {
      containerScan: { ...security.containerScan },
      sastGuardrails: {
        ...security.sastGuardrails,
        sastLanguages: [...security.sastGuardrails.sastLanguages],
      },
    },
  }
}

function matchGroup(content: string, regex: RegExp): string {
  const match = content.match(regex)
  return match?.[1]?.trim() ?? ''
}

export function inferInfraManifestKind(path: string): InfraManifestKind {
  const lower = path.toLowerCase()
  const base = lower.split('/').pop() || lower
  if (lower.includes('/k8s/') || lower.includes('/manifests/') || lower.includes('/kustomize/')) {
    if (base === 'deployment.yaml' || base === 'deployment.yml' || /-deployment\.ya?ml$/.test(base)) {
      return 'k8s-deployment'
    }
    if (base === 'service.yaml' || base === 'service.yml' || /-service\.ya?ml$/.test(base)) {
      return 'k8s-service'
    }
    if (base === 'namespace.yaml') return 'k8s-namespace'
    if (base === 'hpa.yaml') return 'k8s-hpa'
    if (base === 'vpa.yaml') return 'k8s-vpa'
    if (base === 'pdb.yaml') return 'k8s-pdb'
    if (base === 'ingress.yaml') return 'k8s-ingress'
    if (base === 'configmap.yaml') return 'k8s-configmap'
    if (base === 'secret.yaml') return 'k8s-secret'
    if (base === 'serviceaccount.yaml') return 'k8s-serviceaccount'
    if (base === 'networkpolicy.yaml') return 'k8s-networkpolicy'
    if (base === 'resourcequota.yaml') return 'k8s-resourcequota'
    if (base === 'limitrange.yaml') return 'k8s-limitrange'
  }
  if (lower.includes('/helm/') && /values(\.[a-z0-9_-]+)?\.ya?ml$/.test(lower)) return 'helm-values'
  if (lower.includes('/terraform/') || lower.includes('/opentofu/')) {
    return lower.includes('/opentofu/') ? 'opentofu' : 'terraform'
  }
  if (lower.includes('/pulumi/') || base === 'pulumi.yaml' || base === 'index.ts') return 'pulumi'
  if (
    lower.includes('/workflows/') ||
    lower.endsWith('/deploy.yml') ||
    lower.endsWith('/deploy.yaml') ||
    lower.includes('/github/')
  ) return 'github-workflow'
  if (lower.endsWith('.gitlab-ci.yml') || lower.includes('/gitlab/')) return 'gitlab-ci'
  return 'unknown'
}

function inferKind(path: string): InfraManifestKind {
  return inferInfraManifestKind(path)
}

function parseImageParts(image: string): { repo: string; tag: string } {
  if (!image) return { repo: 'nginx', tag: 'latest' }
  const idx = image.lastIndexOf(':')
  if (idx === -1) return { repo: image, tag: 'latest' }
  return { repo: image.slice(0, idx), tag: image.slice(idx + 1) || 'latest' }
}

/** Compose `repo:tag` for docker inspect / deploy (matches serialize image line). */
export function composeImageRef(repo: string, tag: string): string {
  const image = repo.trim()
  const imageTag = tag.trim()
  if (!image) return ''
  if (!imageTag || image.includes(':')) return image
  return `${image}:${imageTag}`
}

/** NodePort is only valid on NodePort / LoadBalancer Services. */
export function serviceUsesNodePort(
  serviceType: InfraManifestModel['serviceType'],
): boolean {
  return serviceType === 'NodePort' || serviceType === 'LoadBalancer'
}

function stripQuotes(value: string): string {
  return value.trim().replace(/^["']|["']$/g, '')
}

/** Read `metadata.name` without nested regex backtracking. */
function parseMetadataName(content: string): string {
  const lines = content.split('\n')
  let inMetadata = false
  let metadataIndent = -1
  for (const line of lines) {
    if (!inMetadata) {
      const start = line.match(/^(\s*)metadata:\s*$/)
      if (!start) continue
      inMetadata = true
      metadataIndent = start[1]?.length ?? 0
      continue
    }
    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (line.trim() !== '' && indent <= metadataIndent) break
    const nameMatch = line.match(/^\s*name:\s*(.+)\s*$/)
    if (nameMatch) return stripQuotes(nameMatch[1] ?? '')
  }
  return ''
}

function parseFirstBareLabel(content: string, key: string): string {
  const pattern = new RegExp(`^\\s*${key}:\\s*(.+)\\s*$`, 'm')
  const match = content.match(pattern)
  return stripQuotes(match?.[1] ?? '')
}

function parseYamlEnv(content: string): KeyValueItem[] {
  const vars: KeyValueItem[] = []
  const lines = content.split('\n')
  let inEnv = false
  let envIndent = -1
  let pendingName: string | null = null

  for (const line of lines) {
    if (!inEnv) {
      const start = line.match(/^(\s*)env:\s*$/)
      if (!start) continue
      inEnv = true
      envIndent = start[1]?.length ?? 0
      continue
    }

    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (line.trim() !== '' && indent <= envIndent) {
      break
    }

    const nameMatch = line.match(/^\s*-\s*name:\s*(.+)\s*$/)
    if (nameMatch) {
      pendingName = stripQuotes(nameMatch[1] ?? '')
      continue
    }
    const valueMatch = line.match(/^\s*value:\s*(.+)\s*$/)
    if (valueMatch && pendingName) {
      vars.push({ key: pendingName, value: stripQuotes(valueMatch[1] ?? '') })
      pendingName = null
    }
  }
  return vars
}

function parseInlineEnvMap(content: string): KeyValueItem[] {
  const vars: KeyValueItem[] = []
  const lines = content.split('\n')
  let inEnv = false
  let envIndent = -1

  for (const line of lines) {
    if (!inEnv) {
      const start = line.match(/^(\s*)env:\s*$/)
      if (!start) continue
      inEnv = true
      envIndent = start[1]?.length ?? 0
      continue
    }

    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (line.trim() !== '' && indent <= envIndent) {
      break
    }

    const m = line.match(/^\s*([A-Za-z_][\w.-]*)\s*:\s*(.+)\s*$/)
    if (!m) continue
    vars.push({
      key: m[1] ?? '',
      value: stripQuotes(m[2] ?? ''),
    })
  }
  return vars
}

/** Parse ConfigMap `data:` or Secret `stringData:` / `data:` maps. */
function parseDataMap(content: string): KeyValueItem[] {
  const vars: KeyValueItem[] = []
  const lines = content.split('\n')
  let inData = false
  let dataIndent = -1

  for (const line of lines) {
    if (!inData) {
      const start = line.match(/^(\s*)(stringData|data):\s*$/)
      if (!start) continue
      inData = true
      dataIndent = start[1]?.length ?? 0
      continue
    }

    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (line.trim() !== '' && indent <= dataIndent) {
      break
    }

    const m = line.match(/^\s*([A-Za-z_][\w.-]*)\s*:\s*(.+)\s*$/)
    if (!m) continue
    vars.push({
      key: m[1] ?? '',
      value: stripQuotes(m[2] ?? ''),
    })
  }
  return vars
}

/** Line-scan resource blocks — nested regexes here previously caused ReDoS freezes. */
function parseResourceBlock(
  content: string,
  section: 'requests' | 'limits',
): { cpu: string; memory: string } {
  const lines = content.split('\n')
  let inSection = false
  let sectionIndent = -1
  let cpu = ''
  let memory = ''

  for (const line of lines) {
    if (!inSection) {
      const start = line.match(new RegExp(`^(\\s*)${section}:\\s*$`))
      if (!start) continue
      inSection = true
      sectionIndent = start[1]?.length ?? 0
      continue
    }

    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (line.trim() !== '' && indent <= sectionIndent) {
      break
    }

    const cpuMatch = line.match(/^\s*cpu:\s*(.+)\s*$/)
    if (cpuMatch) {
      cpu = stripQuotes(cpuMatch[1] ?? '')
      continue
    }
    const memoryMatch = line.match(/^\s*memory:\s*(.+)\s*$/)
    if (memoryMatch) {
      memory = stripQuotes(memoryMatch[1] ?? '')
    }
  }

  return { cpu, memory }
}

function parseSecrets(content: string): KeyValueItem[] {
  const secrets: KeyValueItem[] = []
  for (const match of content.matchAll(/([A-Z_][A-Z0-9_]*)\s*[:=]\s*["']?\${{\s*secrets\.([A-Z0-9_]+)\s*}}["']?/g)) {
    secrets.push({ key: match[1] ?? '', value: match[2] ?? '' })
  }
  for (const match of content.matchAll(/([A-Z_][A-Z0-9_]*)\s*[:=]\s*\$([A-Z_][A-Z0-9_]*)/g)) {
    secrets.push({ key: match[1] ?? '', value: match[2] ?? '' })
  }
  return secrets
}

export function parseInfraManifest(path: string, content: string): InfraManifestModel {
  const model = cloneDefault()
  model.kind = inferKind(path)
  if (path.toLowerCase().includes('/helm/') && path.toLowerCase().includes('values.')) {
    model.helmValueFile = path.split('/').pop() || 'values.yaml'
  }

  if (model.kind === 'k8s-namespace') {
    model.resourceName = parseMetadataName(content)
    model.namespaceName = model.resourceName
    return model
  }

  if (model.kind === 'k8s-deployment') {
    const image = matchGroup(content, /image:\s*([^\n]+)/)
    const parsed = parseImageParts(stripQuotes(image))
    model.resourceName = parseMetadataName(content)
    model.appLabel = parseFirstBareLabel(content, 'app') || 'app'
    model.replicas = Number(matchGroup(content, /replicas:\s*([0-9]+)/) || '1')
    model.appImage = parsed.repo
    model.imageTag = parsed.tag
    model.appPort = matchGroup(content, /containerPort:\s*([0-9]+)/) || model.appPort
    const pullPolicy = matchGroup(content, /imagePullPolicy:\s*(Always|IfNotPresent|Never)/)
    if (pullPolicy === 'Always' || pullPolicy === 'IfNotPresent' || pullPolicy === 'Never') {
      model.pullPolicy = pullPolicy
    }
    model.envVars = parseYamlEnv(content)
    const requests = parseResourceBlock(content, 'requests')
    const limits = parseResourceBlock(content, 'limits')
    model.cpuRequest = requests.cpu
    model.memoryRequest = requests.memory
    model.cpuLimit = limits.cpu
    model.memoryLimit = limits.memory
    return model
  }

  if (model.kind === 'k8s-service') {
    model.resourceName = parseMetadataName(content)
    model.appLabel = parseFirstBareLabel(content, 'app') || 'app'
    model.appPort = matchGroup(content, /port:\s*([0-9]+)/) || model.appPort
    model.targetPort = matchGroup(content, /targetPort:\s*([^\n]+)/) || model.targetPort
    model.nodePort = matchGroup(content, /nodePort:\s*([0-9]+)/) || ''
    const svcType = matchGroup(content, /type:\s*(ClusterIP|NodePort|LoadBalancer)/)
    if (svcType === 'NodePort' || svcType === 'LoadBalancer' || svcType === 'ClusterIP') {
      model.serviceType = svcType
    }
    return model
  }

  if (model.kind === 'k8s-hpa') {
    model.resourceName = parseMetadataName(content)
    model.hpaMinReplicas = Number(matchGroup(content, /minReplicas:\s*([0-9]+)/) || '2')
    model.hpaMaxReplicas = Number(matchGroup(content, /maxReplicas:\s*([0-9]+)/) || '10')
    model.hpaTargetCpu = Number(matchGroup(content, /averageUtilization:\s*([0-9]+)/) || '70')
    return model
  }

  if (model.kind === 'k8s-vpa') {
    model.resourceName = parseMetadataName(content)
    const mode = matchGroup(content, /updateMode:\s*["']?(Off|Initial|Recreate|Auto)["']?/)
    if (mode === 'Off' || mode === 'Initial' || mode === 'Recreate' || mode === 'Auto') {
      model.vpaUpdateMode = mode
    }
    return model
  }

  if (model.kind === 'k8s-pdb') {
    model.resourceName = parseMetadataName(content)
    model.appLabel = parseFirstBareLabel(content, 'app') || 'app'
    model.pdbMinAvailable = matchGroup(content, /minAvailable:\s*([^\n]+)/) || '1'
    return model
  }

  if (model.kind === 'k8s-ingress') {
    model.resourceName = parseMetadataName(content)
    model.ingressHost = matchGroup(content, /host:\s*([^\n]+)/) || ''
    model.ingressClassName = matchGroup(content, /ingressClassName:\s*([^\n]+)/) || 'nginx'
    model.ingressPath = matchGroup(content, /path:\s*([^\n]+)/) || '/'
    return model
  }

  if (model.kind === 'k8s-configmap' || model.kind === 'k8s-secret') {
    model.resourceName = parseMetadataName(content)
    model.dataEntries = parseDataMap(content)
    return model
  }

  if (model.kind === 'k8s-serviceaccount') {
    model.resourceName = parseMetadataName(content)
    return model
  }

  if (model.kind === 'k8s-networkpolicy') {
    model.resourceName = parseMetadataName(content)
    model.appLabel = parseFirstBareLabel(content, 'app') || 'app'
    return model
  }

  if (model.kind === 'k8s-resourcequota') {
    model.resourceName = parseMetadataName(content)
    model.quotaCpuRequests = matchGroup(content, /requests\.cpu:\s*["']?([^\n"']+)/) || model.quotaCpuRequests
    model.quotaMemoryRequests = matchGroup(content, /requests\.memory:\s*["']?([^\n"']+)/) || model.quotaMemoryRequests
    model.quotaPods = matchGroup(content, /pods:\s*["']?([^\n"']+)/) || model.quotaPods
    return model
  }

  if (model.kind === 'k8s-limitrange') {
    model.resourceName = parseMetadataName(content)
    model.limitDefaultCpu = matchGroup(content, /default:[\s\S]*?cpu:\s*["']?([^\n"']+)/) || model.limitDefaultCpu
    model.limitDefaultMemory = matchGroup(content, /default:[\s\S]*?memory:\s*["']?([^\n"']+)/) || model.limitDefaultMemory
    return model
  }

  if (model.kind === 'helm-values') {
    model.appImage = stripQuotes(matchGroup(content, /repository:\s*([^\n]+)/)) || model.appImage
    model.imageTag = stripQuotes(matchGroup(content, /tag:\s*([^\n]+)/)) || model.imageTag
    model.appPort =
      readYamlBlockField(content, 'service', 'port')
      || matchGroup(content, /servicePort:\s*([0-9]+)/)
      || model.appPort
    model.targetPort =
      readYamlBlockField(content, 'service', 'targetPort')
      || model.targetPort
    model.nodePort = readYamlBlockField(content, 'service', 'nodePort') || ''
    model.replicas = Number(matchGroup(content, /replicaCount:\s*([0-9]+)/) || '1')
    const pullPolicy = matchGroup(content, /pullPolicy:\s*(Always|IfNotPresent|Never)/)
    if (pullPolicy === 'Always' || pullPolicy === 'IfNotPresent' || pullPolicy === 'Never') {
      model.pullPolicy = pullPolicy
    }
    const svcType =
      readYamlBlockField(content, 'service', 'type')
      || matchGroup(content, /(?:serviceType|type):\s*(ClusterIP|NodePort|LoadBalancer)/)
    if (svcType === 'NodePort' || svcType === 'LoadBalancer' || svcType === 'ClusterIP') {
      model.serviceType = svcType
    }
    const requests = parseResourceBlock(content, 'requests')
    const limits = parseResourceBlock(content, 'limits')
    model.cpuRequest = requests.cpu
    model.memoryRequest = requests.memory
    model.cpuLimit = limits.cpu
    model.memoryLimit = limits.memory
    model.envVars = parseInlineEnvMap(content)
    return model
  }

  if (model.kind === 'terraform' || model.kind === 'opentofu') {
    model.region =
      matchGroup(content, /region\s*=\s*"([^"]+)"/) ||
      matchGroup(content, /variable\s+"region"[\s\S]*?default\s*=\s*"([^"]+)"/m)
    const instanceMatch = content.match(/(?:instance_type|machine_type)\s*=\s*"([^"]+)"/)
    model.instanceSize = instanceMatch?.[1] ?? ''
    const clusterMatch = content.match(/(?:node_count|cluster_size)\s*=\s*([0-9]+)/)
    model.clusterSize = clusterMatch?.[1] ?? ''
    model.resourceCount = Number(matchGroup(content, /count\s*=\s*([0-9]+)/) || '1')
    return model
  }

  if (model.kind === 'pulumi') {
    model.region =
      matchGroup(content, /region\s*=\s*"([^"]+)"/) ||
      stripQuotes(matchGroup(content, /region:\s*([^\n]+)/))
    model.instanceSize = matchGroup(content, /machineType\s*[:=]\s*"([^"]+)"/)
    model.clusterSize = matchGroup(content, /nodeCount\s*[:=]\s*([0-9]+)/)
    model.resourceCount = Number(matchGroup(content, /count\s*[:=]\s*([0-9]+)/) || '1')
    return model
  }

  if (model.kind === 'github-workflow') {
    model.branch = matchGroup(content, /branches:\s*\["([^"]+)"\]/) || matchGroup(content, /-\s*([A-Za-z0-9/_-]+)\s*$/m) || 'main'
    model.runner = matchGroup(content, /runs-on:\s*([^\n]+)/) || 'ubuntu-latest'
    model.buildSecrets = parseSecrets(content)
    model.cicdSecurity = inferCicdSecurityFromContent(content)
    return model
  }

  if (model.kind === 'gitlab-ci') {
    model.branch = matchGroup(content, /\$CI_COMMIT_BRANCH == "([^"]+)"/)
      || matchGroup(content, /-\s*([A-Za-z0-9/_-]+)\s*$/m)
      || 'main'
    model.runner = matchGroup(content, /image:\s*([^\n]+)/) || 'docker:27'
    model.buildSecrets = parseSecrets(content)
    model.cicdSecurity = inferCicdSecurityFromContent(content)
    return model
  }

  return model
}

function replaceOrAppend(content: string, regex: RegExp, replacement: string, append: string): string {
  const source = regex.source
  const flags = regex.flags.replace('g', '')
  const probe = new RegExp(source, flags)
  if (probe.test(content)) {
    return content.replace(new RegExp(source, flags), replacement)
  }
  return `${content.trimEnd()}\n${append}\n`
}

/** Replace bare `key: value` lines (does not touch dotted keys like app.kubernetes.io/name). */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function replaceBareKeyLines(content: string, key: string, value: string): string {
  const pattern = new RegExp(`^(\\s*)${escapeRegExp(key)}:\\s*.+$`, 'gm')
  if (!new RegExp(pattern.source, 'm').test(content)) {
    return content
  }
  return content.replace(pattern, `$1${key}: ${value}`)
}

/** Locate a top-level YAML mapping block (`service:`) and its child line range. */
function findTopLevelYamlBlock(
  content: string,
  blockKey: string,
): { start: number; end: number; lines: string[] } | null {
  const lines = content.split('\n')
  const start = lines.findIndex((line) => new RegExp(`^${escapeRegExp(blockKey)}:\\s*(?:#.*)?$`).test(line))
  if (start < 0) return null
  let end = lines.length
  for (let i = start + 1; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    if (!line.trim() || line.trim().startsWith('#')) continue
    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (indent === 0) {
      end = i
      break
    }
  }
  return { start, end, lines }
}

function readYamlBlockField(content: string, blockKey: string, field: string): string {
  const block = findTopLevelYamlBlock(content, blockKey)
  if (!block) return ''
  const fieldRe = new RegExp(`^\\s*${escapeRegExp(field)}:\\s*(.+?)\\s*$`)
  for (let i = block.start + 1; i < block.end; i += 1) {
    const match = (block.lines[i] ?? '').match(fieldRe)
    if (match?.[1]) return stripQuotes(match[1])
  }
  return ''
}

function upsertYamlBlockField(
  content: string,
  blockKey: string,
  field: string,
  value: string,
): string {
  const block = findTopLevelYamlBlock(content, blockKey)
  const childIndent = '  '
  if (!block) {
    return `${content.trimEnd()}\n${blockKey}:\n${childIndent}${field}: ${value}\n`
  }
  const { start, end, lines } = block
  const fieldRe = new RegExp(`^(\\s*)${escapeRegExp(field)}:\\s*.*$`)
  for (let i = start + 1; i < end; i += 1) {
    if (fieldRe.test(lines[i] ?? '')) {
      lines[i] = `${childIndent}${field}: ${value}`
      return lines.join('\n')
    }
  }
  lines.splice(end, 0, `${childIndent}${field}: ${value}`)
  return lines.join('\n')
}

function removeYamlBlockField(content: string, blockKey: string, field: string): string {
  const block = findTopLevelYamlBlock(content, blockKey)
  if (!block) return content
  const fieldRe = new RegExp(`^\\s*${escapeRegExp(field)}:\\s*`)
  const next = [
    ...block.lines.slice(0, block.start + 1),
    ...block.lines.slice(block.start + 1, block.end).filter((line) => !fieldRe.test(line)),
    ...block.lines.slice(block.end),
  ]
  return next.join('\n')
}

/** Drop port keys that escaped the ``service:`` block (invalid YAML / helm load failures). */
function stripOrphanHelmServicePortLines(content: string): string {
  const block = findTopLevelYamlBlock(content, 'service')
  const lines = content.split('\n')
  const keep: string[] = []
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    const inService = Boolean(block && i > block.start && i < block.end)
    if (!inService && /^\s*(port|targetPort|nodePort):\s*/.test(line)) {
      continue
    }
    keep.push(line)
  }
  return keep.join('\n')
}

function replaceTopLevelYamlBlock(content: string, blockKey: string, replacement: string): string {
  const block = findTopLevelYamlBlock(content, blockKey)
  const replacementLines = replacement.replace(/\n$/, '').split('\n')
  if (!block) {
    return `${content.trimEnd()}\n${replacementLines.join('\n')}\n`
  }
  return [
    ...block.lines.slice(0, block.start),
    ...replacementLines,
    ...block.lines.slice(block.end),
  ].join('\n')
}

function replaceMetadataName(content: string, name: string): string {
  const lines = content.split('\n')
  let inMetadata = false
  let metadataIndent = -1
  let replaced = false
  const next = lines.map((line) => {
    if (!inMetadata) {
      const start = line.match(/^(\s*)metadata:\s*$/)
      if (start) {
        inMetadata = true
        metadataIndent = start[1]?.length ?? 0
      }
      return line
    }
    const indent = line.match(/^(\s*)/)?.[1]?.length ?? 0
    if (line.trim() !== '' && indent <= metadataIndent) {
      inMetadata = false
      return line
    }
    if (!replaced && /^\s*name:\s*/.test(line)) {
      replaced = true
      return line.replace(/^(\s*name:\s*).+$/, `$1${name}`)
    }
    return line
  })
  return next.join('\n')
}

function envYamlList(vars: KeyValueItem[], envIndent: string): string {
  if (!vars.length) return ''
  const itemIndent = `${envIndent}  `
  const fieldIndent = `${envIndent}    `
  return vars
    .map((item) => `${itemIndent}- name: ${item.key}\n${fieldIndent}value: "${item.value}"`)
    .join('\n')
}

function envYamlMap(vars: KeyValueItem[]): string {
  if (!vars.length) return 'env: {}\n'
  return `env:\n${vars.map((item) => `  ${item.key}: "${item.value}"`).join('\n')}\n`
}

function lineIndentOf(line: string): number {
  return line.match(/^(\s*)/)?.[1]?.length ?? 0
}

/**
 * Locate a YAML mapping key and the contiguous deeper-indented body that follows.
 * Avoids regex lookaheads that stop early on nested keys like ``cpu:``.
 */
function findYamlKeyBlock(
  lines: string[],
  key: string,
): { start: number; end: number; indent: string } | null {
  const keyPattern = new RegExp(`^(\\s*)${escapeRegExp(key)}:\\s*(?:\\S.*)?$`)
  for (let i = 0; i < lines.length; i += 1) {
    const match = lines[i]?.match(keyPattern)
    if (!match) continue
    const indent = match[1] ?? ''
    const indentLen = indent.length
    let end = i + 1
    while (end < lines.length) {
      const line = lines[end] ?? ''
      if (line.trim() === '') {
        let peek = end + 1
        while (peek < lines.length && (lines[peek] ?? '').trim() === '') peek += 1
        if (peek >= lines.length || lineIndentOf(lines[peek] ?? '') <= indentLen) {
          break
        }
        end += 1
        continue
      }
      if (lineIndentOf(line) <= indentLen) break
      end += 1
    }
    return { start: i, end, indent }
  }
  return null
}

function replaceYamlKeyBlock(content: string, key: string, block: string): string {
  const lines = content.split('\n')
  const range = findYamlKeyBlock(lines, key)
  if (!range) {
    return `${content.trimEnd()}\n${block}\n`
  }
  return [...lines.slice(0, range.start), block, ...lines.slice(range.end)].join('\n')
}

function detectContainerFieldIndent(content: string): string {
  const imageLine = content.match(/^(\s*)image:\s*\S+/m)
  if (imageLine?.[1] != null) return imageLine[1]
  return '          '
}

function buildResourcesBlock(
  indent: string,
  model: Pick<InfraManifestModel, 'cpuRequest' | 'memoryRequest' | 'cpuLimit' | 'memoryLimit'>,
): string {
  const i2 = `${indent}  `
  const i4 = `${indent}    `
  return [
    `${indent}resources:`,
    `${i2}requests:`,
    `${i4}cpu: ${model.cpuRequest || '100m'}`,
    `${i4}memory: ${model.memoryRequest || '128Mi'}`,
    `${i2}limits:`,
    `${i4}cpu: "${model.cpuLimit || '250m'}"`,
    `${i4}memory: ${model.memoryLimit || '256Mi'}`,
  ].join('\n')
}

export function serializeInfraManifest(
  path: string,
  content: string,
  model: InfraManifestModel,
): string {
  const kind = inferKind(path)
  let next = content

  if (kind === 'k8s-namespace') {
    const name = model.namespaceName || model.resourceName
    if (name) next = replaceMetadataName(next, name)
    return next
  }

  if (kind === 'k8s-deployment') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    if (model.appLabel) {
      next = replaceBareKeyLines(next, 'app', model.appLabel)
      next = replaceBareKeyLines(next, 'app.kubernetes.io/name', model.appLabel)
    }
    next = replaceOrAppend(
      next,
      /replicas:\s*[0-9]+/,
      `replicas: ${Math.max(1, model.replicas || 1)}`,
      `replicas: ${Math.max(1, model.replicas || 1)}`,
    )
    next = replaceOrAppend(
      next,
      /image:\s*[^\n]+/,
      `image: ${model.appImage}:${model.imageTag}`,
      `image: ${model.appImage}:${model.imageTag}`,
    )
    next = replaceOrAppend(
      next,
      /containerPort:\s*[0-9]+/,
      `containerPort: ${model.appPort || '80'}`,
      `containerPort: ${model.appPort || '80'}`,
    )
    next = replaceOrAppend(
      next,
      /imagePullPolicy:\s*(Always|IfNotPresent|Never)/,
      `imagePullPolicy: ${model.pullPolicy}`,
      `imagePullPolicy: ${model.pullPolicy}`,
    )
    if (model.envVars.length) {
      const lines = next.split('\n')
      const existing = findYamlKeyBlock(lines, 'env')
      const envIndent = existing?.indent ?? detectContainerFieldIndent(next)
      const envBlock = `${envIndent}env:\n${envYamlList(model.envVars, envIndent)}`
      next = replaceYamlKeyBlock(next, 'env', envBlock)
    }
    if (model.cpuRequest || model.memoryRequest || model.cpuLimit || model.memoryLimit) {
      const lines = next.split('\n')
      const existing = findYamlKeyBlock(lines, 'resources')
      const indent = existing?.indent ?? detectContainerFieldIndent(next)
      next = replaceYamlKeyBlock(next, 'resources', buildResourcesBlock(indent, model))
    }
    return next
  }

  if (kind === 'k8s-service') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    if (model.appLabel) {
      next = replaceBareKeyLines(next, 'app', model.appLabel)
      next = replaceBareKeyLines(next, 'app.kubernetes.io/name', model.appLabel)
    }
    next = replaceOrAppend(
      next,
      /type:\s*(ClusterIP|NodePort|LoadBalancer)/,
      `type: ${model.serviceType}`,
      `type: ${model.serviceType}`,
    )
    next = replaceOrAppend(next, /port:\s*[0-9]+/, `port: ${model.appPort || '80'}`, `port: ${model.appPort || '80'}`)
    next = replaceOrAppend(
      next,
      /targetPort:\s*[^\n]+/,
      `targetPort: ${model.targetPort || model.appPort || '80'}`,
      `targetPort: ${model.targetPort || model.appPort || '80'}`,
    )
    if (model.serviceType === 'NodePort' || model.serviceType === 'LoadBalancer') {
      if (model.nodePort.trim()) {
        next = replaceOrAppend(
          next,
          /nodePort:\s*[0-9]+/,
          `nodePort: ${model.nodePort.trim()}`,
          `      nodePort: ${model.nodePort.trim()}`,
        )
      } else {
        // Leave allocation to the control plane / Launchpad assigner.
        next = next.replace(/^[ \t]*nodePort:\s*[0-9]+[ \t]*\n?/gm, '')
      }
    } else {
      next = next.replace(/^[ \t]*nodePort:\s*[0-9]+[ \t]*\n?/gm, '')
    }
    return next
  }

  if (kind === 'k8s-hpa') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    next = replaceOrAppend(next, /minReplicas:\s*[0-9]+/, `minReplicas: ${Math.max(1, model.hpaMinReplicas)}`, `minReplicas: ${Math.max(1, model.hpaMinReplicas)}`)
    next = replaceOrAppend(next, /maxReplicas:\s*[0-9]+/, `maxReplicas: ${Math.max(1, model.hpaMaxReplicas)}`, `maxReplicas: ${Math.max(1, model.hpaMaxReplicas)}`)
    next = replaceOrAppend(
      next,
      /averageUtilization:\s*[0-9]+/,
      `averageUtilization: ${Math.max(1, Math.min(100, model.hpaTargetCpu))}`,
      `averageUtilization: ${Math.max(1, Math.min(100, model.hpaTargetCpu))}`,
    )
    return next
  }

  if (kind === 'k8s-vpa') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    next = replaceOrAppend(
      next,
      /updateMode:\s*["']?(Off|Initial|Recreate|Auto)["']?/,
      `updateMode: "${model.vpaUpdateMode}"`,
      `updateMode: "${model.vpaUpdateMode}"`,
    )
    return next
  }

  if (kind === 'k8s-pdb') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    if (model.appLabel) next = replaceBareKeyLines(next, 'app', model.appLabel)
    next = replaceOrAppend(
      next,
      /minAvailable:\s*[^\n]+/,
      `minAvailable: ${model.pdbMinAvailable || '1'}`,
      `minAvailable: ${model.pdbMinAvailable || '1'}`,
    )
    return next
  }

  if (kind === 'k8s-ingress') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    if (model.ingressHost) {
      next = replaceOrAppend(next, /host:\s*[^\n]+/, `host: ${model.ingressHost}`, `host: ${model.ingressHost}`)
    }
    if (model.ingressClassName) {
      next = replaceOrAppend(
        next,
        /ingressClassName:\s*[^\n]+/,
        `ingressClassName: ${model.ingressClassName}`,
        `ingressClassName: ${model.ingressClassName}`,
      )
    }
    if (model.ingressPath) {
      next = replaceOrAppend(next, /path:\s*[^\n]+/, `path: ${model.ingressPath}`, `path: ${model.ingressPath}`)
    }
    return next
  }

  if (kind === 'k8s-configmap' || kind === 'k8s-secret') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    if (model.dataEntries.length) {
      const blockKey = kind === 'k8s-secret' && /stringData:/.test(next) ? 'stringData' : 'data'
      const dataBlock = `${blockKey}:\n${model.dataEntries.map((item) => `  ${item.key}: "${item.value}"`).join('\n')}\n`
      next = replaceOrAppend(
        next,
        /(stringData|data):\s*\n(?:\s+[A-Za-z_][\w.-]*:\s*[^\n]*\n?)*/m,
        dataBlock,
        dataBlock,
      )
    }
    return next
  }

  if (kind === 'k8s-serviceaccount' || kind === 'k8s-networkpolicy') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    if (kind === 'k8s-networkpolicy' && model.appLabel) {
      next = replaceBareKeyLines(next, 'app', model.appLabel)
    }
    return next
  }

  if (kind === 'k8s-resourcequota') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    next = replaceOrAppend(next, /requests\.cpu:\s*[^\n]+/, `requests.cpu: "${model.quotaCpuRequests}"`, `requests.cpu: "${model.quotaCpuRequests}"`)
    next = replaceOrAppend(next, /requests\.memory:\s*[^\n]+/, `requests.memory: ${model.quotaMemoryRequests}`, `requests.memory: ${model.quotaMemoryRequests}`)
    next = replaceOrAppend(next, /pods:\s*[^\n]+/, `pods: "${model.quotaPods}"`, `pods: "${model.quotaPods}"`)
    return next
  }

  if (kind === 'k8s-limitrange') {
    if (model.resourceName) next = replaceMetadataName(next, model.resourceName)
    return next
  }

  if (kind === 'helm-values') {
    next = replaceOrAppend(next, /repository:\s*[^\n]+/, `repository: ${model.appImage}`, `repository: ${model.appImage}`)
    next = replaceOrAppend(next, /tag:\s*[^\n]+/, `tag: "${model.imageTag}"`, `tag: "${model.imageTag}"`)
    next = replaceOrAppend(
      next,
      /replicaCount:\s*[0-9]+/,
      `replicaCount: ${Math.max(1, model.replicas || 1)}`,
      `replicaCount: ${Math.max(1, model.replicas || 1)}`,
    )
    next = upsertYamlBlockField(next, 'service', 'type', model.serviceType)
    next = upsertYamlBlockField(next, 'service', 'port', model.appPort || '80')
    next = upsertYamlBlockField(
      next,
      'service',
      'targetPort',
      model.targetPort || model.appPort || '80',
    )
    if (model.serviceType === 'NodePort' || model.serviceType === 'LoadBalancer') {
      if (model.nodePort.trim()) {
        next = upsertYamlBlockField(next, 'service', 'nodePort', model.nodePort.trim())
      } else {
        next = removeYamlBlockField(next, 'service', 'nodePort')
      }
    } else {
      next = removeYamlBlockField(next, 'service', 'nodePort')
    }
    next = stripOrphanHelmServicePortLines(next)
    next = replaceOrAppend(next, /pullPolicy:\s*(Always|IfNotPresent|Never)/, `pullPolicy: ${model.pullPolicy}`, `pullPolicy: ${model.pullPolicy}`)
    next = replaceOrAppend(
      next,
      /^\s*env:\s*\n[\s\S]*?(?=^\S|\Z)/m,
      envYamlMap(model.envVars),
      envYamlMap(model.envVars),
    )
    if (model.cpuRequest || model.memoryRequest || model.cpuLimit || model.memoryLimit) {
      const resourcesBlock = [
        'resources:',
        '  requests:',
        `    cpu: ${model.cpuRequest || '100m'}`,
        `    memory: ${model.memoryRequest || '128Mi'}`,
        '  limits:',
        `    cpu: ${model.cpuLimit || '250m'}`,
        `    memory: ${model.memoryLimit || '256Mi'}`,
      ].join('\n')
      next = replaceTopLevelYamlBlock(next, 'resources', resourcesBlock)
    }
    return next
  }

  if (kind === 'terraform' || kind === 'opentofu') {
    if (model.region) {
      next = replaceOrAppend(next, /region\s*=\s*"[^"]+"/, `region = "${model.region}"`, `region = "${model.region}"`)
    }
    if (model.instanceSize) {
      next = replaceOrAppend(
        next,
        /(instance_type|machine_type)\s*=\s*"[^"]+"/,
        `instance_type = "${model.instanceSize}"`,
        `instance_type = "${model.instanceSize}"`,
      )
    }
    if (model.clusterSize) {
      next = replaceOrAppend(next, /(node_count|cluster_size)\s*=\s*[0-9]+/, `node_count = ${model.clusterSize}`, `node_count = ${model.clusterSize}`)
    }
    next = replaceOrAppend(next, /count\s*=\s*[0-9]+/, `count = ${Math.max(1, model.resourceCount)}`, `count = ${Math.max(1, model.resourceCount)}`)
    return next
  }

  if (kind === 'pulumi') {
    if (model.region) {
      next = replaceOrAppend(
        next,
        /region\s*[:=]\s*["']?[^"'\n]+["']?/,
        `region = "${model.region}"`,
        `region = "${model.region}"`,
      )
    }
    if (model.instanceSize) {
      next = replaceOrAppend(
        next,
        /machineType\s*[:=]\s*["']?[^"'\n]+["']?/,
        `machineType = "${model.instanceSize}"`,
        `machineType = "${model.instanceSize}"`,
      )
    }
    if (model.clusterSize) {
      next = replaceOrAppend(next, /nodeCount\s*[:=]\s*[0-9]+/, `nodeCount = ${model.clusterSize}`, `nodeCount = ${model.clusterSize}`)
    }
    return next
  }

  if (kind === 'github-workflow' || kind === 'gitlab-ci') {
    const platform: CicdPlatform = kind === 'github-workflow' ? 'github' : 'gitlab'
    return renderCicdWorkflow(platform, model.cicdSecurity ?? defaultCicdSecurityConfig(platform), {
      branch: model.branch || 'main',
      runner: model.runner || (platform === 'github' ? 'ubuntu-latest' : 'docker:27'),
    })
  }

  return content
}
