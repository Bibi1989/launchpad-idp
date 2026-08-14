<script setup lang="ts">
const props = defineProps<{
  projectId?: string | null
  projectName?: string | null
}>()

const label = computed(() => (props.projectName || '').trim() || 'Project')
const href = computed(() =>
  props.projectId ? `/projects/${props.projectId}` : null,
)
</script>

<template>
  <NuxtLink
    v-if="href"
    :to="href"
    class="inline-flex max-w-[180px] items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--lp-muted)] transition hover:border-[var(--lp-accent)]/40 hover:text-[var(--lp-accent)]"
    :title="label"
    @click.stop
  >
    <span class="material-symbols-outlined text-sm">folder</span>
    <span class="truncate">{{ label }}</span>
  </NuxtLink>
</template>
