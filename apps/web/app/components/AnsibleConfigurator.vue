<script setup lang="ts">
import type { AnsibleConfig } from '~/types/provisioning'
import { defaultAnsibleConfig } from '~/utils/cloudValidation'

const props = withDefaults(
  defineProps<{
    modelValue: AnsibleConfig
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: AnsibleConfig]
}>()

const { t } = useI18n()

const config = computed({
  get: () => props.modelValue ?? defaultAnsibleConfig(),
  set: (value: AnsibleConfig) => emit('update:modelValue', value),
})

function patch(partial: Partial<AnsibleConfig>) {
  config.value = { ...config.value, ...partial, enabled: true }
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

    <div v-if="config.enabled" class="space-y-5">
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
  </section>
</template>
