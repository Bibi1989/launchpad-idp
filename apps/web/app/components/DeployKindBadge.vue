<script setup lang="ts">
import type { DeployKind } from '~/utils/deployKind'
import { resolveDeployKind } from '~/utils/deployKind'

const { t } = useI18n()

const props = defineProps<{
  deployMode?: string | null
  kind?: DeployKind | null
}>()

const kind = computed(() => props.kind || resolveDeployKind(props.deployMode))

const label = computed(() => {
  switch (kind.value) {
    case 'docker':
      return t('environments.deployKind.docker')
    case 'instance':
      return t('environments.deployKind.instance')
    default:
      return t('environments.deployKind.kubernetes')
  }
})

const icon = computed(() => {
  switch (kind.value) {
    case 'docker':
      return 'deployed_code'
    case 'instance':
      return 'dns'
    default:
      return 'hub'
  }
})

const tone = computed(() => {
  switch (kind.value) {
    case 'docker':
      return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
    case 'instance':
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
    :title="t('environments.deployKind.title')"
  >
    <span class="material-symbols-outlined text-sm">{{ icon }}</span>
    {{ label }}
  </span>
</template>
