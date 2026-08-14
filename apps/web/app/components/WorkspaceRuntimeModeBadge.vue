<script setup lang="ts">
import type { WorkspaceRuntimeMode } from '~/types/provisioning'

const { t } = useI18n()

const props = defineProps<{
  runtimeMode?: WorkspaceRuntimeMode | string | null
}>()

const mode = computed(() => props.runtimeMode || 'kubernetes')

const label = computed(() => {
  switch (mode.value) {
    case 'docker_compose':
      return t('provision.runtimeMode.modes.docker_compose.title')
    case 'running_instance':
      return t('provision.runtimeMode.modes.running_instance.title')
    default:
      return t('provision.runtimeMode.modes.kubernetes.title')
  }
})

const icon = computed(() => {
  switch (mode.value) {
    case 'docker_compose':
      return 'deployed_code'
    case 'running_instance':
      return 'dns'
    default:
      return 'hub'
  }
})

const tone = computed(() => {
  switch (mode.value) {
    case 'docker_compose':
      return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
    case 'running_instance':
      return 'border-violet-500/30 bg-violet-500/10 text-violet-300'
    default:
      return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  }
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
    :class="tone"
    :title="t('provision.runtimeMode.title')"
  >
    <span class="material-symbols-outlined text-sm">{{ icon }}</span>
    {{ label }}
  </span>
</template>
