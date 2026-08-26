<script setup lang="ts">
import type { AnsibleConfig, CloudProvider, RunningInstanceConfig } from '~/types/provisioning'
import { defaultAnsibleConfig } from '~/utils/cloudValidation'
import { buildAnsibleScaffold } from '~/utils/ansibleScaffold'
import {
  ansibleDeployModeFromStrategy,
  resolveProcessStrategy,
} from '~/utils/instanceComputeTargets'

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
const { apiFetch } = useApi()

const WorkspaceMonacoEditor = defineAsyncComponent(
  () => import('~/components/WorkspaceMonacoEditor.vue'),
)

type AnsibleTab = 'form' | 'advanced' | 'cloud' | 'ai'
type AdvancedFileKey = 'inventory' | 'playbook' | 'groupVars'

const activeTab = ref<AnsibleTab>('form')
const advancedFile = ref<AdvancedFileKey>('inventory')
const advancedDirty = ref(false)
const inventoryContent = ref('')
const playbookContent = ref('')
const groupVarsContent = ref('')
const aiPrompt = ref('')
const aiBusy = ref(false)
const aiSummary = ref<string | null>(null)
const aiError = ref<string | null>(null)

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

function enrichPackages(
  base: string[],
  mode: AnsibleConfig['app_deploy_mode'],
  proxy: NonNullable<AnsibleConfig['reverse_proxy']>,
): string[] {
  const next = new Set(base.length ? base : ['curl', 'ca-certificates', 'gnupg', 'jq', 'htop'])
  if (mode === 'pm2') {
    next.add('nodejs')
    next.add('npm')
  }
  if (proxy === 'nginx') next.add('nginx')
  if (proxy === 'caddy') next.add('debian-keyring')
  return Array.from(next)
}

function syncFromRunningInstance() {
  const ri = props.runningInstance
  if (!ri || ri.kind === 'serverless') return

  const strategy = resolveProcessStrategy(ri)
  const mode = ansibleDeployModeFromStrategy(strategy)
  const reverse_proxy = ri.reverse_proxy || 'none'
  // ansibleDeployModeFromStrategy only ever yields docker_run / systemd / pm2.
  const install_docker = mode === 'docker_run'
  const listen = ri.listen_port || config.value.app_listen_port || 8080

  let groups = [...(config.value.deploy_user_groups || [])]
  if (install_docker) {
    if (!groups.includes('docker')) groups.push('docker')
  } else {
    groups = groups.filter((g) => g !== 'docker')
  }

  const ports = new Set<number>(config.value.ufw_allow_ports?.length ? config.value.ufw_allow_ports : [22])
  ports.add(22)
  ports.add(listen)
  if (reverse_proxy !== 'none') {
    ports.add(80)
    ports.add(443)
  }
  const ufw_allow_ports = Array.from(ports).sort((a, b) => a - b)
  const packages = enrichPackages(config.value.packages || [], mode, reverse_proxy)
  const hosts = (ri.host || '').trim() || config.value.hosts || '127.0.0.1'
  const ssh_user = ri.ssh_user || config.value.ssh_user
  const ssh_port = ri.ssh_port || config.value.ssh_port
  const ssh_private_key_path = ri.ssh_key_path || config.value.ssh_private_key_path
  const app_start_command = config.value.app_start_command || 'npm start'

  const same =
    config.value.app_deploy_mode === mode
    && (config.value.reverse_proxy || 'none') === reverse_proxy
    && config.value.install_docker === install_docker
    && config.value.app_listen_port === listen
    && config.value.hosts === hosts
    && config.value.ssh_user === ssh_user
    && config.value.ssh_port === ssh_port
    && (config.value.ssh_private_key_path || null) === (ssh_private_key_path || null)
    && config.value.enabled === true
    && JSON.stringify(config.value.ufw_allow_ports || []) === JSON.stringify(ufw_allow_ports)
    && JSON.stringify(config.value.packages || []) === JSON.stringify(packages)
  if (same) return

  patch({
    enabled: true,
    app_deploy_mode: mode,
    reverse_proxy,
    install_docker,
    install_compose_plugin: install_docker,
    deploy_user_groups: groups,
    packages,
    ufw_allow_ports,
    hosts,
    ssh_user,
    ssh_port,
    ssh_private_key_path,
    app_listen_port: listen,
    app_start_command,
  })
}

watch(
  () => [
    props.runningInstance?.process_strategy,
    props.runningInstance?.reverse_proxy,
    props.runningInstance?.listen_port,
    props.runningInstance?.host,
    props.runningInstance?.kind,
  ],
  () => {
    // Defer so parent wizard apply/config settles before we emit ansible patches.
    nextTick(() => syncFromRunningInstance())
  },
  { immediate: true },
)

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
  syncFromRunningInstance()
}

async function refineWithAi() {
  aiError.value = null
  aiSummary.value = null
  const prompt = aiPrompt.value.trim()
  if (prompt.length < 3) {
    aiError.value = t('scaffold.ansible.aiPromptRequired')
    return
  }
  aiBusy.value = true
  try {
    const current = buildWritableFiles()
    const result = await apiFetch<{
      files: Array<{ path: string; content: string }>
      summary: string
      source: string
    }>('/ai/refine-ansible', {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        workspace_name: props.workspaceName || 'launchpad-workspace',
        app_deploy_mode: config.value.app_deploy_mode,
        reverse_proxy: config.value.reverse_proxy || 'none',
        files: current.map((f) => ({ path: f.path, content: f.content })),
      }),
      timeoutMs: 120_000,
    })
    const byPath = new Map(result.files.map((f) => [f.path, f.content]))
    if (byPath.has(ADVANCED_PATHS.inventory)) {
      inventoryContent.value = byPath.get(ADVANCED_PATHS.inventory) || ''
    }
    if (byPath.has(ADVANCED_PATHS.playbook)) {
      playbookContent.value = byPath.get(ADVANCED_PATHS.playbook) || ''
    }
    if (byPath.has(ADVANCED_PATHS.groupVars)) {
      groupVarsContent.value = byPath.get(ADVANCED_PATHS.groupVars) || ''
      const gv = groupVarsContent.value
      const modeMatch = gv.match(/app_deploy_mode:\s*(\S+)/)
      const proxyMatch = gv.match(/reverse_proxy:\s*(\S+)/)
      const dockerMatch = gv.match(/install_docker:\s*(true|false)/i)
      patch({
        ...(modeMatch?.[1]
          ? { app_deploy_mode: modeMatch[1] as AnsibleConfig['app_deploy_mode'] }
          : {}),
        ...(proxyMatch?.[1]
          ? { reverse_proxy: proxyMatch[1] as AnsibleConfig['reverse_proxy'] }
          : {}),
        ...(dockerMatch?.[1]
          ? { install_docker: dockerMatch[1].toLowerCase() === 'true' }
          : {}),
      })
    }
    advancedDirty.value = true
    activeTab.value = 'advanced'
    aiSummary.value = result.summary || t('scaffold.ansible.aiUpdated')
  } catch (err) {
    aiError.value = err instanceof Error ? err.message : t('scaffold.ansible.aiFailed')
  } finally {
    aiBusy.value = false
  }
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
  return base.map((file) => {
    const override = overrides[file.path]
    return override != null ? { ...file, content: override } : file
  })
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
          v-for="tab in (['form', 'advanced', 'cloud', 'ai'] as AnsibleTab[])"
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
                : tab === 'cloud'
                  ? t('scaffold.ansible.tabCloud')
                  : t('scaffold.ansible.tabAi')
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
                <option value="pm2">{{ t('scaffold.ansible.modePm2') }}</option>
                <option value="none">{{ t('scaffold.ansible.modeNone') }}</option>
              </select>
            </label>
            <label class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.ansible.reverseProxy') }}</span>
              <select
                class="lp-input text-xs"
                :value="config.reverse_proxy || 'none'"
                :disabled="disabled"
                @change="patch({ reverse_proxy: ($event.target as HTMLSelectElement).value as AnsibleConfig['reverse_proxy'] })"
              >
                <option value="none">{{ t('provision.runtimeMode.attach.proxies.none') }}</option>
                <option value="nginx">{{ t('provision.runtimeMode.attach.proxies.nginx') }}</option>
                <option value="caddy">{{ t('provision.runtimeMode.attach.proxies.caddy') }}</option>
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

      <div v-show="activeTab === 'ai'" class="space-y-3">
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('scaffold.ansible.aiBlurb') }}
        </p>
        <p class="text-[11px] text-[var(--lp-muted)]">
          {{ t('scaffold.ansible.aiContext', {
            mode: config.app_deploy_mode,
            proxy: config.reverse_proxy || 'none',
          }) }}
        </p>
        <label class="block space-y-1">
          <span class="lp-label">{{ t('scaffold.ansible.aiPrompt') }}</span>
          <textarea
            v-model="aiPrompt"
            class="lp-input min-h-[100px] text-sm"
            :disabled="disabled || aiBusy"
            :placeholder="t('scaffold.ansible.aiPromptPlaceholder')"
          />
        </label>
        <div
          v-if="aiError"
          class="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
        >
          {{ aiError }}
        </div>
        <div
          v-if="aiSummary"
          class="rounded-lg border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-3 py-2 text-xs text-[var(--lp-text)]"
        >
          {{ aiSummary }}
        </div>
        <button
          type="button"
          class="lp-btn-primary text-xs px-3 py-1.5"
          :disabled="disabled || aiBusy"
          @click="refineWithAi"
        >
          {{ aiBusy ? t('scaffold.ansible.aiUpdating') : t('scaffold.ansible.aiUpdate') }}
        </button>
      </div>
    </div>
  </section>
</template>
