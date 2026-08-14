<script setup lang="ts">
import type { ImageSecurityScanConfig, KubernetesImageSource } from '~/types/provisioning'

const source = defineModel<KubernetesImageSource>('source', { required: true })
const imageScan = defineModel<ImageSecurityScanConfig>('imageScan')
const { t } = useI18n()

const props = defineProps<{
  cloudProvider?: string | null
}>()

const showImageScan = computed(
  () =>
    Boolean(imageScan.value)
    && source.value === 'build_registry'
    && Boolean(props.cloudProvider)
    && props.cloudProvider !== 'local',
)
</script>

<template>
  <div class="space-y-3 rounded-xl border border-[var(--lp-line)] p-3">
    <p class="lp-label">{{ t('launch.kubernetesImageSource') }}</p>
    <div class="space-y-2 text-sm">
      <label class="flex items-start gap-3">
        <input
          v-model="source"
          type="radio"
          value="build_registry"
          class="mt-1 accent-[var(--lp-accent)]"
        >
        <span>
          <span class="font-medium text-[var(--lp-text)]">{{ t('launch.kubernetesBuildRegistry') }}</span>
          <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">
            {{ cloudProvider && cloudProvider !== 'local'
              ? t('launch.kubernetesBuildRegistryCloudHint', { provider: cloudProvider.toUpperCase() })
              : t('launch.kubernetesBuildRegistryLocalHint') }}
          </span>
        </span>
      </label>
      <label class="flex items-start gap-3">
        <input
          v-model="source"
          type="radio"
          value="external"
          class="mt-1 accent-[var(--lp-accent)]"
        >
        <span>
          <span class="font-medium text-[var(--lp-text)]">{{ t('launch.kubernetesExternalImage') }}</span>
          <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">{{ t('launch.kubernetesExternalImageHint') }}</span>
        </span>
      </label>
    </div>
    <ImageSecurityScanPicker
      v-if="showImageScan && imageScan"
      v-model:scan="imageScan"
    />
  </div>
</template>
