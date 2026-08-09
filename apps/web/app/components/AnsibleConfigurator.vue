<script setup lang="ts">
import type { AnsibleConfig, CloudProvider, RunningInstanceConfig } from '~/types/provisioning'
import { defaultAnsibleConfig } from '~/utils/cloudValidation'
import { buildAnsibleScaffold } from '~/utils/ansibleScaffold'

const props = withDefaults(
  defineProps<{
    modelValue: AnsibleConfig
    disabled?: boolean
    cloudProvider?: CloudProvider
    runningInstance?: RunningInstanceConfig | null
    workspaceName?: string
  }>(),
  {
    disabled: false,
    cloudProvider: 'local',
    runningInstance: null,
    workspaceName: 'launchpad-workspace',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: AnsibleConfig]
}>()

const { t } = useI18n()

const WorkspaceMonacoEditor = defineAsyncComponent(
  () => import('~/components/WorkspaceMonacoEditor.vue'),
)

type AnsibleTab = 'form' | 'advanced' | 'cloud'
type AdvancedFileKey = 'inventory' | 'playbook' | 'groupVars'

const activeTab = ref<AnsibleTab>('form')
const advancedFile = ref<AdvancedFileKey>('inventory')
const advancedDirty = ref(false)
const inventoryContent = ref('')
const playbookContent = ref('')
const groupVarsContent = ref('')

const config = computed({
  get: () => props.modelValue ?? defaultAnsibleConfig(),
  set: (value: AnsibleConfig) => emit('update:modelValue', value),
})

function patch(partial: Partial<AnsibleConfig>) {
  config.value = { ...config.value, ...partial, enabled: partial.enabled ?? true }
  if (partial.enabled === false) return
  // Form edits refresh Advanced unless the user has custom YAML.
  if (activeTab.value !== 'advanced' || !advancedDirty.value) {
    regenerateAdvancedFromForm()
  }
}

const packagesText = computed({
  get: () => (config.value.packages || []).join(', '),
  set: (raw: string) => {
    const packages = raw
      .split(/[,\n]/)
      .map((p) => p.trim())
      .filter(Boolean)
    patch({ packages })
  },
})

const portsText = computed({
  get: () => (config.value.ufw_allow_ports || []).join(', '),
  set: (raw: string) => {
    const ufw_allow_ports = raw
      .split(/[,\n]/)
      .map((p) => Number.parseInt(p.trim(), 10))
      .filter((n) => Number.isFinite(n) && n >= 1 && n <= 65535)
    patch({ ufw_allow_ports: ufw_allow_ports.length ? ufw_allow_ports : [22] })
  },
})

const ADVANCED_PATHS: Record<AdvancedFileKey, string> = {
  inventory: 'infra/ansible/inventory/hosts.yml',
  playbook: 'infra/ansible/playbooks/site.yml',
  groupVars: 'infra/ansible/group_vars/all.yml',
}

function regenerateAdvancedFromForm() {
  const files = buildAnsibleScaffold(props.workspaceName || 'launchpad-workspace', {
    ...config.value,
    enabled: true,
  })
  const byPath = new Map(files.map((f) => [f.path, f.content]))
  inventoryContent.value = byPath.get(ADVANCED_PATHS.inventory) || ''
  playbookContent.value = byPath.get(ADVANCED_PATHS.playbook) || ''
  groupVarsContent.value = byPath.get(ADVANCED_PATHS.groupVars) || ''
  advancedDirty.value = false
}

function activeAdvancedContent(): string {
  if (advancedFile.value === 'playbook') return playbookContent.value
  if (advancedFile.value === 'groupVars') return groupVarsContent.value
  return inventoryContent.value
}

function setActiveAdvancedContent(value: string) {
  advancedDirty.value = true
  if (advancedFile.value === 'playbook') {
    playbookContent.value = value
    return
  }
  if (advancedFile.value === 'groupVars') {
    groupVarsContent.value = value
    return
  }
  inventoryContent.value = value
}

const advancedEditorModel = computed({
  get: () => activeAdvancedContent(),
  set: (value: string) => setActiveAdvancedContent(value),
})

const advancedPath = computed(() => ADVANCED_PATHS[advancedFile.value])

watch(
  () => config.value.enabled,
  (enabled) => {
    if (enabled) regenerateAdvancedFromForm()
  },
  { immediate: true },
)

function applyCloudFromInstance() {
  const ri = props.runningInstance
  if (!ri) return
  patch({
    hosts: (ri.host || '').trim() || config.value.hosts,
    ssh_user: ri.ssh_user || config.value.ssh_user,
    ssh_port: ri.ssh_port || config.value.ssh_port,
    ssh_private_key_path: ri.ssh_key_path || config.value.ssh_private_key_path,
    app_listen_port: ri.listen_port || config.value.app_listen_port,
  })
}

function applyCloudDefaults() {
  const provider = props.cloudProvider || 'local'
  if (provider === 'aws') {
    patch({
      ssh_user: 'ec2-user',
      packages: Array.from(
        new Set([...(config.value.packages || []), 'amazon-ssm-agent']),
      ),
    })
    return
  }
  if (provider === 'gcp') {
    patch({ ssh_user: 'ubuntu' })
    return
  }
  if (provider === 'azure') {
    patch({ ssh_user: 'azureuser' })
    return
  }
  patch({ hosts: config.value.hosts || '127.0.0.1', ssh_user: 'ubuntu' })
}

/** Files for clients to write under infra/ansible (form + Advanced overrides). */
function buildWritableFiles(): Array<{ path: string; content: string }> {
  const base = buildAnsibleScaffold(props.workspaceName || 'launchpad-workspace', {
    ...config.value,
    enabled: true,
  })
  if (!advancedDirty.value) return base
  const overrides: Record<string, string> = {
    [ADVANCED_PATHS.inventory]: inventoryContent.value,
    [ADVANCED_PATHS.playbook]: playbookContent.value,
    [ADVANCED_PATHS.groupVars]: groupVarsContent.value,
  }
  return base.map((file) =>
    overrides[file.path] != null
      ? { ...file, content: overrides[file.path] }
      : file,
  )
}

defineExpose({ buildWritableFiles, regenerateAdvancedFromForm })
</script>

<template>
  <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/40 p-4">
    <div class="flex items-start justify-between gap-3">
      <div>
        <p class="lp-label">{{ t('scaffold.ansible.label') }}</p>
        <h3 class="text-base font-semibold text-[var(--lp-text)]">
          {{ t('scaffold.ansible.title') }}
        </h3>
        <p class="mt-1 text-xs text-[var(--lp-muted)]">
          {{ t('scaffold.ansible.blurb') }}
        </p>
      </div>
      <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
        <input
          type="checkbox"
          class="accent-[var(--lp-accent)]"
          :checked="config.enabled"
          :disabled="disabled"
          @change="patch({ enabled: ($event.target as HTMLInputElement).checked })"
        >
        {{ t('scaffold.ansible.enable') }}
      </label>
    </div>

    <div v-if="config.enabled" class="space-y-4">
      <div class="flex flex-wrap gap-1 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-1">
        <button
          v-for="tab in (['form', 'advanced', 'cloud'] as AnsibleTab[])"
          :key="tab"
          type="button"
          class="rounded-md px-3 py-1.5 text-xs font-medium transition"
          :class="
            activeTab === tab
              ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)]'
              : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
          "
          @click="activeTab = tab"
        >
          {{
            tab === 'form'
              ? t('scaffold.ansible.tabForm')
              : tab === 'advanced'
                ? t('scaffold.ansible.tabAdvanced')
                : t('scaffold.ansible.tabCloud')
          }}
        </button>
      </div>

      <div v-show="activeTab === 'form'" class="space-y-5">
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block space-y-1 sm:col-span-2">
            <span class="lp-label">{{ t('scaffold.ansible.hosts') }}</span>
            <textarea
              class="lp-input min-h-[72px] font-mono text-xs"
              :value="config.hosts"
              :disabled="disabled"
              :placeholder="t('scaffold.ansible.hostsPlaceholder')"
              @input="patch({ hosts: ($event.target as HTMLTextAreaElement).value })"
            />
          </label>
          <label class="block space-y-1">
            <span class="lp-label">{{ t('scaffold.ansible.group') }}</span>
            <input
              class="lp-input text-xs"
              :value="config.inventory_group"
              :disabled="disabled"
              @input="patch({ inventory_group: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <label class="block space-y-1">
            <span class="lp-label">{{ t('scaffold.ansible.timezone') }}</span>
            <input
              class="lp-input text-xs"
              :value="config.timezone"
              :disabled="disabled"
              @input="patch({ timezone: ($event.target as HTMLInputElement).value })"
            >
          </label>
        </div>

        <div>
          <p class="lp-label mb-2">{{ t('scaffold.ansible.connection') }}</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.sshUser') }}</span>
              <input
                class="lp-input text-xs"
                :value="config.ssh_user"
                :disabled="disabled"
                @input="patch({ ssh_user: ($event.target as HTMLInputElement).value })"
              >
            </label>
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.sshPort') }}</span>
              <input
                type="number"
                class="lp-input text-xs"
                :value="config.ssh_port"
                :disabled="disabled"
                @input="patch({ ssh_port: Number(($event.target as HTMLInputElement).value) || 22 })"
              >
            </label>
            <label class="block space-y-1 sm:col-span-2">
              <span class="lp-label">{{ t('scaffold.ansible.sshKey') }}</span>
              <input
                class="lp-input font-mono text-xs"
                :value="config.ssh_private_key_path || ''"
                :disabled="disabled"
                placeholder="~/.ssh/id_ed25519"
                @input="patch({ ssh_private_key_path: ($event.target as HTMLInputElement).value || null })"
              >
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.become"
                :disabled="disabled"
                @change="patch({ become: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.become') }}
            </label>
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.becomeUser') }}</span>
              <input
                class="lp-input text-xs"
                :value="config.become_user"
                :disabled="disabled || !config.become"
                @input="patch({ become_user: ($event.target as HTMLInputElement).value })"
              >
            </label>
          </div>
        </div>

        <div>
          <p class="lp-label mb-2">{{ t('scaffold.ansible.bootstrap') }}</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.set_hostname"
                :disabled="disabled"
                @change="patch({ set_hostname: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.setHostname') }}
            </label>
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.hostname') }}</span>
              <input
                class="lp-input text-xs"
                :value="config.hostname || ''"
                :disabled="disabled || !config.set_hostname"
                @input="patch({ hostname: ($event.target as HTMLInputElement).value || null })"
              >
            </label>
            <label class="block space-y-1 sm:col-span-2">
              <span class="lp-label">{{ t('scaffold.ansible.packages') }}</span>
              <input
                v-model="packagesText"
                class="lp-input font-mono text-xs"
                :disabled="disabled"
              >
            </label>
          </div>
        </div>

        <div>
          <p class="lp-label mb-2">{{ t('scaffold.ansible.docker') }}</p>
          <div class="flex flex-wrap gap-4">
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.install_docker"
                :disabled="disabled"
                @change="patch({ install_docker: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.installDocker') }}
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.install_compose_plugin"
                :disabled="disabled || !config.install_docker"
                @change="patch({ install_compose_plugin: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.installCompose') }}
            </label>
          </div>
        </div>

        <div>
          <p class="lp-label mb-2">{{ t('scaffold.ansible.hardening') }}</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.enable_ufw"
                :disabled="disabled"
                @change="patch({ enable_ufw: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.ufw') }}
            </label>
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.ufwPorts') }}</span>
              <input
                v-model="portsText"
                class="lp-input font-mono text-xs"
                :disabled="disabled || !config.enable_ufw"
              >
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.enable_fail2ban"
                :disabled="disabled"
                @change="patch({ enable_fail2ban: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.fail2ban') }}
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.enable_unattended_upgrades"
                :disabled="disabled"
                @change="patch({ enable_unattended_upgrades: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.unattended') }}
            </label>
          </div>
        </div>

        <div>
          <p class="lp-label mb-2">{{ t('scaffold.ansible.app') }}</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.appMode') }}</span>
              <select
                class="lp-input text-xs"
                :value="config.app_deploy_mode"
                :disabled="disabled"
                @change="patch({ app_deploy_mode: ($event.target as HTMLSelectElement).value as AnsibleConfig['app_deploy_mode'] })"
              >
                <option value="docker_run">{{ t('scaffold.ansible.modeDockerRun') }}</option>
                <option value="docker_compose">{{ t('scaffold.ansible.modeCompose') }}</option>
                <option value="systemd">{{ t('scaffold.ansible.modeSystemd') }}</option>
                <option value="none">{{ t('scaffold.ansible.modeNone') }}</option>
              </select>
            </label>
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.appPort') }}</span>
              <input
                type="number"
                class="lp-input text-xs"
                :value="config.app_listen_port"
                :disabled="disabled"
                @input="patch({ app_listen_port: Number(($event.target as HTMLInputElement).value) || 8080 })"
              >
            </label>
            <label class="block space-y-1 sm:col-span-2">
              <span class="lp-label">{{ t('scaffold.ansible.appDir') }}</span>
              <input
                class="lp-input font-mono text-xs"
                :value="config.app_dir"
                :disabled="disabled"
                @input="patch({ app_dir: ($event.target as HTMLInputElement).value })"
              >
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.sync_workspace"
                :disabled="disabled"
                @change="patch({ sync_workspace: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.syncWorkspace') }}
            </label>
            <label class="flex items-center gap-2 text-xs text-[var(--lp-text)]">
              <input
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :checked="config.create_deploy_user"
                :disabled="disabled"
                @change="patch({ create_deploy_user: ($event.target as HTMLInputElement).checked })"
              >
              {{ t('scaffold.ansible.deployUser') }}
            </label>
          </div>
        </div>
      </div>

      <div v-show="activeTab === 'advanced'" class="space-y-3">
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('scaffold.ansible.advancedHint') }}
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="rounded px-2.5 py-1 font-mono text-[11px] transition"
            :class="
              advancedFile === 'inventory'
                ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)] font-semibold'
                : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
            "
            @click="advancedFile = 'inventory'"
          >
            {{ t('scaffold.ansible.fileInventory') }}
          </button>
          <button
            type="button"
            class="rounded px-2.5 py-1 font-mono text-[11px] transition"
            :class="
              advancedFile === 'playbook'
                ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)] font-semibold'
                : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
            "
            @click="advancedFile = 'playbook'"
          >
            {{ t('scaffold.ansible.filePlaybook') }}
          </button>
          <button
            type="button"
            class="rounded px-2.5 py-1 font-mono text-[11px] transition"
            :class="
              advancedFile === 'groupVars'
                ? 'bg-[var(--lp-accent)]/20 text-[var(--lp-accent)] font-semibold'
                : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'
            "
            @click="advancedFile = 'groupVars'"
          >
            {{ t('scaffold.ansible.fileGroupVars') }}
          </button>
          <button
            type="button"
            class="lp-btn-ghost ml-auto text-xs"
            :disabled="disabled || !advancedDirty"
            @click="regenerateAdvancedFromForm"
          >
            {{ t('scaffold.ansible.resetAdvanced') }}
          </button>
        </div>
        <div
          class="flex h-[min(52vh,480px)] min-h-[280px] flex-col overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30"
        >
          <ClientOnly>
            <WorkspaceMonacoEditor
              v-model="advancedEditorModel"
              :path="advancedPath"
              class="min-h-0 flex-1"
            />
            <template #fallback>
              <p class="p-4 text-sm text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
            </template>
          </ClientOnly>
        </div>
      </div>

      <div v-show="activeTab === 'cloud'" class="space-y-4">
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('scaffold.ansible.cloudHint') }}
        </p>
        <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/30 px-3 py-2 text-xs text-[var(--lp-text)]">
          <span class="lp-label">{{ t('scaffold.ansible.cloudProvider') }}</span>
          <p class="mt-1 font-mono uppercase tracking-wide text-[var(--lp-accent)]">
            {{ cloudProvider }}
          </p>
          <p class="mt-1 text-[11px] text-[var(--lp-muted)]">
            {{ t('scaffold.ansible.cloudProviderHint') }}
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="lp-btn-primary text-xs px-3 py-1.5"
            :disabled="disabled"
            @click="applyCloudDefaults"
          >
            {{ t('scaffold.ansible.applyCloudDefaults') }}
          </button>
          <button
            type="button"
            class="lp-btn-ghost text-xs px-3 py-1.5"
            :disabled="disabled || !runningInstance?.host"
            @click="applyCloudFromInstance"
          >
            {{ t('scaffold.ansible.syncFromInstance') }}
          </button>
        </div>
        <label class="block space-y-1">
          <span class="lp-label">{{ t('scaffold.ansible.hosts') }}</span>
          <textarea
            class="lp-input min-h-[72px] font-mono text-xs"
            :value="config.hosts"
            :disabled="disabled"
            :placeholder="t('scaffold.ansible.hostsPlaceholder')"
            @input="patch({ hosts: ($event.target as HTMLTextAreaElement).value })"
          />
        </label>
      </div>
    </div>
  </section>
</template>
