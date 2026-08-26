import type { CloudProviderCatalogEntry, CloudServiceGroup } from '../cloud-providers.types'
import { SERVICE_CATALOG } from './provider-services'

const PARENT_CLOUDS = [
  'gcp',
  'aws',
  'azure',
  'cloudflare',
  'hetzner',
  'digitalocean',
  'linode',
  'railway',
  'render',
] as const

const RUNTIME_TARGETS: Record<string, CloudProviderCatalogEntry['runtime_targets']> = {
  kubernetes: ['kubernetes'],
  docker: ['docker_host'],
  vm: ['vm'],
  paas: ['paas'],
}

const ICONS: Record<string, string> = {
  gke: 'hub',
  'cloud-run': 'directions_run',
  'gce-docker': 'deployed_code',
  gce: 'computer',
  eks: 'hub',
  'ecs-fargate': 'sailing',
  'ec2-docker': 'deployed_code',
  ec2: 'computer',
  aks: 'hub',
  'container-apps': 'view_in_ar',
  aci: 'inventory_2',
  'vm-docker': 'deployed_code',
  'azure-vm': 'desktop_windows',
  workers: 'bolt',
  pages: 'web',
  k3s: 'hub',
  'server-docker': 'deployed_code',
  'cloud-server': 'dns',
  doks: 'hub',
  'app-platform': 'apps',
  'droplet-docker': 'deployed_code',
  droplet: 'water_drop',
  lke: 'hub',
  'linode-docker': 'deployed_code',
  'linode-instance': 'computer',
  'railway-service': 'rocket_launch',
  'render-web': 'web',
  'render-worker': 'precision_manufacturing',
}

const PARENT_ICONS: Record<string, string> = {
  gcp: 'cloud_sync',
  aws: 'cloud_upload',
  azure: 'cloud_queue',
  cloudflare: 'cyclone',
  hetzner: 'dns',
  digitalocean: 'water_drop',
  linode: 'computer',
  railway: 'rocket_launch',
  render: 'web',
}

export function splitPluginId(providerId: string): [string, string | null] {
  const pid = (providerId || '').trim().toLowerCase()
  if (pid.endsWith('-legacy')) return [pid, null]
  const parents = [...PARENT_CLOUDS].sort((a, b) => b.length - a.length)
  for (const parent of parents) {
    if (pid === parent) return [parent, null]
    const prefix = `${parent}-`
    if (pid.startsWith(prefix)) {
      const rest = pid.slice(prefix.length)
      if (rest) return [parent, rest]
    }
  }
  return [pid, null]
}

export function adapterIdFor(providerId: string): string {
  const [parent] = splitPluginId(providerId)
  return parent
}

export function expandServicePlugins(
  baseCatalog: CloudProviderCatalogEntry[],
): CloudProviderCatalogEntry[] {
  const parents: CloudProviderCatalogEntry[] = []
  const extra: CloudProviderCatalogEntry[] = []
  const byId = new Map<string, CloudProviderCatalogEntry>()
  for (const entry of baseCatalog) {
    const row: CloudProviderCatalogEntry = {
      ...entry,
      parent_cloud: entry.parent_cloud ?? null,
      source: entry.source ?? 'builtin',
      icon: entry.icon ?? PARENT_ICONS[entry.id] ?? 'cloud',
    }
    parents.push(row)
    byId.set(row.id, row)
  }

  for (const [parentId, groups] of Object.entries(SERVICE_CATALOG) as Array<[string, CloudServiceGroup[]]>) {
    const parent = byId.get(parentId)
    if (!parent) continue
    for (const group of groups) {
      const targets = RUNTIME_TARGETS[group.runtime] ?? parent.runtime_targets
      for (const svc of group.services) {
        extra.push({
          ...parent,
          id: `${parentId}-${svc.id}`,
          label: svc.label,
          description: svc.description,
          icon: ICONS[svc.id] ?? PARENT_ICONS[parentId] ?? 'cloud',
          parent_cloud: parentId,
          service: svc.id,
          source: 'builtin-plugin',
          runtime_targets: targets,
          services: [
            {
              runtime: group.runtime,
              label: group.label,
              services: [svc],
            },
          ],
        })
      }
    }
  }
  return [...parents, ...extra]
}

export function catalogOverlayFor(
  providerId: string,
  parentEntry: CloudProviderCatalogEntry,
): CloudProviderCatalogEntry {
  const [parent, service] = splitPluginId(providerId)
  if (!service) {
    return { ...parentEntry, services: parentEntry.services ?? [] }
  }
  const expanded = expandServicePlugins([parentEntry])
  const match = expanded.find((item) => item.id === providerId)
  if (match) return match
  return {
    ...parentEntry,
    id: providerId,
    parent_cloud: parent,
    service,
    source: 'builtin-plugin',
  }
}

export function mergeCatalog<T extends { id: string }>(base: T[], extra: T[]): T[] {
  const byId = new Map<string, T>()
  const order: string[] = []
  for (const row of [...base, ...extra]) {
    if (!row.id) continue
    if (!byId.has(row.id)) order.push(row.id)
    byId.set(row.id, row)
  }
  return order.map((id) => byId.get(id)).filter((row): row is T => Boolean(row))
}
