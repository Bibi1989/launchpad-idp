<script setup lang="ts">
import type { ProvisioningTool } from '~/types/cloudProviders'

const DEFAULT_TOOLS: ProvisioningTool[] = [
  {
    id: 'cloud-init',
    label: 'LaunchConfig',
    category: 'config',
    description: 'Default. Configures the instance to run your app (Docker, env, systemd / cloud-init).',
    supported_clouds: ['*'],
    implemented: true,
    default: true,
  },
  {
    id: 'ansible',
    label: 'Ansible',
    category: 'config',
    description: 'Agentless playbooks under infra/ansible. Use this when you want to customize host setup.',
    supported_clouds: ['*'],
    implemented: true,
    default: false,
  },
  {
    id: 'puppet',
    label: 'Puppet',
    category: 'config',
    description: 'Optional. Register a Puppet config plugin to configure VMs after provision.',
    supported_clouds: ['*'],
    docs_url: 'https://www.puppet.com/docs',
    implemented: true,
    default: false,
  },
  {
    id: 'chef',
    label: 'Chef',
    category: 'config',
    description: 'Optional. Register a Chef config plugin to configure VMs after provision.',
    supported_clouds: ['*'],
    docs_url: 'https://docs.chef.io/',
    implemented: true,
    default: false,
  },
]

const selected = defineModel<string>({ default: 'cloud-init' })

const props = withDefaults(
  defineProps<{
    provider?: string
    disabled?: boolean
  }>(),
  { provider: 'gcp', disabled: false },
)

const { t } = useI18n()
const { load, toolsForCloud } = useProvisioningTools()

onMounted(() => {
  void load()
})

const tools = computed(() => {
  const fromApi = toolsForCloud(props.provider).config
  return fromApi.length ? fromApi : DEFAULT_TOOLS
})

watch(
  tools,
  (list) => {
    if (!list.length) return
    const current = (selected.value || '').trim()
    if (current && list.some((tool) => tool.id === current)) return
    selected.value = list.find((tool) => tool.default)?.id ?? list[0]?.id ?? 'cloud-init'
  },
  { immediate: true },
)
</script>

<template>
  <div class="space-y-2">
    <span class="lp-label">{{ t('provision.configTool') }}</span>
    <p class="text-xs text-[var(--lp-muted)]">{{ t('provision.configToolHint') }}</p>
    <div class="grid gap-2 sm:grid-cols-2">
      <label
        v-for="tool in tools"
        :key="tool.id"
        class="flex items-start gap-2 rounded-lg border p-3 text-xs"
        :class="[
          selected === tool.id ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10' : 'border-[var(--lp-line)]',
          tool.implemented === false || disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
        ]"
      >
        <input
          v-model="selected"
          type="radio"
          :value="tool.id"
          class="mt-0.5"
          :disabled="disabled || tool.implemented === false"
        >
        <span class="flex flex-col">
          <span class="font-medium text-[var(--lp-text)]">{{ tool.label }}</span>
          <span class="text-[10px] text-[var(--lp-muted)]">{{ tool.description }}</span>
          <span
            v-if="tool.implemented === false"
            class="text-[10px] text-[var(--lp-warn,#eab308)]"
          >
            {{ t('cloudPlugins.registerConfigPlugin') }}
          </span>
        </span>
      </label>
    </div>
  </div>
</template>
