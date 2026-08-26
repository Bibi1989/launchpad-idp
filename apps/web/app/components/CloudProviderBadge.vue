<script setup lang="ts">
/**
 * Small badge showing which cloud an environment or workspace runs on
 * (local, gcp, aws, azure, cloudflare, ...). Mirrors the DeployKindBadge /
 * WorkspaceRuntimeModeBadge visual pattern (icon + label + tone).
 */
const props = defineProps<{
  provider?: string | null
}>()

const normalized = computed(() => (props.provider || 'local').trim().toLowerCase())

const LABELS: Record<string, string> = {
  local: 'Local',
  gcp: 'GCP',
  aws: 'AWS',
  azure: 'Azure',
  cloudflare: 'Cloudflare',
  hetzner: 'Hetzner',
  digitalocean: 'DigitalOcean',
  linode: 'Linode',
  railway: 'Railway',
  render: 'Render',
}

const ICONS: Record<string, string> = {
  local: 'computer',
  gcp: 'cloud_sync',
  aws: 'cloud_upload',
  azure: 'cloud_queue',
  cloudflare: 'cyclone',
  hetzner: 'dns',
  digitalocean: 'water_drop',
  linode: 'dns',
  railway: 'train',
  render: 'dns',
}

const TONES: Record<string, string> = {
  local: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  gcp: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
  aws: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  azure: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  cloudflare: 'border-orange-500/30 bg-orange-500/10 text-orange-300',
  hetzner: 'border-red-500/30 bg-red-500/10 text-red-300',
  digitalocean: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
}

const label = computed(() => LABELS[normalized.value] || normalized.value.toUpperCase())
const icon = computed(() => ICONS[normalized.value] || 'cloud')
const tone = computed(
  () => TONES[normalized.value] || 'border-slate-500/30 bg-slate-500/10 text-slate-300',
)
</script>

<template>
  <span
    class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
    :class="tone"
    :title="`Cloud: ${label}`"
  >
    <span class="material-symbols-outlined text-sm">{{ icon }}</span>
    {{ label }}
  </span>
</template>
