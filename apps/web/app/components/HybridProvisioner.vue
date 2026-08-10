<script setup lang="ts">
const { t } = useI18n()
const route = useRoute()

// Shared selection: picking a node in the fleet targets it in the AI panel.
const selectedNodeId = ref<string | null>(null)

watch(
  () => route.query.node,
  (value) => {
    if (typeof value === 'string' && value.trim()) {
      selectedNodeId.value = value.trim()
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="w-full animate-fade-up space-y-6 pb-10">
    <header class="space-y-2">
      <p class="lp-label">{{ t('hybrid.eyebrow') }}</p>
      <h1 class="text-2xl font-semibold tracking-tight text-[var(--lp-text)] sm:text-3xl">
        {{ t('hybrid.title') }}
      </h1>
      <p class="max-w-2xl text-sm leading-relaxed text-[var(--lp-muted)]">{{ t('hybrid.subtitle') }}</p>
    </header>

    <!-- items-start keeps the fleet column from stretching with the AI panel height -->
    <div class="grid gap-6 xl:grid-cols-[minmax(18rem,24rem)_minmax(0,1fr)] xl:items-start">
      <aside class="xl:sticky xl:top-4 xl:self-start">
        <NodeFleetPanel v-model="selectedNodeId" />
      </aside>
      <AiProvisionerPanel v-model="selectedNodeId" />
    </div>
  </div>
</template>
