<script setup lang="ts">
import type {
  AiProvisionerStatus,
  BlueprintDeployResponse,
  BlueprintGenerateResponse,
  BlueprintTarget,
} from '~/types/aiProvisioner'
import { toastError } from '~/composables/useToast'

const props = withDefaults(
  defineProps<{
    modelValue?: string | null
  }>(),
  { modelValue: null },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const { t } = useI18n()
const toast = useToast()
const { generateBlueprint, fixBlueprint, deployBlueprint, status } = useAiProvisioner()
const { nodes, refresh: refreshNodes } = useNodes()

const prompt = ref('')
const target = ref<BlueprintTarget>('local_node')
const region = ref('')
const generating = ref(false)
const fixing = ref(false)
const deploying = ref(false)
const result = ref<BlueprintGenerateResponse | null>(null)
const lastDeploy = ref<BlueprintDeployResponse | null>(null)
const providerStatus = ref<AiProvisionerStatus | null>(null)
const logLines = ref<{ text: string; tone: 'info' | 'ok' | 'warn' | 'danger' }[]>([])
const deployFailed = ref(false)

const nodeId = computed({
  get: () => props.modelValue,
  set: (value: string | null) => emit('update:modelValue', value),
})

const onlineNodes = computed(() => nodes.value.filter((n) => n.online))

const targets: { value: BlueprintTarget; labelKey: string }[] = [
  { value: 'local_node', labelKey: 'hybrid.ai.targetNode' },
  { value: 'gcp', labelKey: 'hybrid.ai.targetGcp' },
  { value: 'aws', labelKey: 'hybrid.ai.targetAws' },
  { value: 'azure', labelKey: 'hybrid.ai.targetAzure' },
]

const canGenerate = computed(() => {
  if (prompt.value.trim().length < 4 || generating.value || fixing.value) return false
  if (target.value === 'local_node' && !nodeId.value) return false
  return true
})

const canDeploy = computed(() => {
  if (!result.value || !result.value.validation.valid || deploying.value || fixing.value) return false
  if (target.value === 'local_node' && !nodeId.value) return false
  return true
})

const canFix = computed(() => {
  return Boolean(result.value && deployFailed.value && !fixing.value && !deploying.value && !generating.value)
})

const showDeployResult = computed(() => Boolean(lastDeploy.value?.ok))

onMounted(async () => {
  try {
    providerStatus.value = await status()
  } catch {
    // status is best-effort
  }
  if (!nodes.value.length) {
    await refreshNodes().catch(() => {})
  }
})

function money(value: number): string {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`
}

async function onGenerate() {
  if (!canGenerate.value) return
  generating.value = true
  result.value = null
  lastDeploy.value = null
  deployFailed.value = false
  logLines.value = []
  try {
    result.value = await generateBlueprint({
      prompt: prompt.value.trim(),
      target: target.value,
      node_id: target.value === 'local_node' ? nodeId.value : null,
      region: region.value.trim() || null,
    })
  } catch (err) {
    toast.error(toastError(err, 'Blueprint generation failed'))
  } finally {
    generating.value = false
  }
}

async function onFixWithAi() {
  if (!canFix.value || !result.value) return
  fixing.value = true
  lastDeploy.value = null
  try {
    const errorLog = logLines.value
      .filter((l) => l.tone === 'danger' || /error|failed|not found/i.test(l.text))
      .map((l) => l.text)
      .join('\n')
    result.value = await fixBlueprint({
      blueprint: result.value.blueprint,
      error_log: errorLog || logLines.value.map((l) => l.text).join('\n') || 'Deployment failed',
      prompt: prompt.value.trim() || result.value.blueprint.summary,
      target: target.value,
      node_id: target.value === 'local_node' ? nodeId.value : null,
      region: region.value.trim() || null,
    })
    deployFailed.value = false
    logLines.value = [{
      text: t('hybrid.ai.fixApplied'),
      tone: 'ok',
    }]
    toast.success(t('hybrid.ai.fixApplied'))
  } catch (err) {
    toast.error(toastError(err, t('hybrid.ai.fixFailed')))
  } finally {
    fixing.value = false
  }
}

async function onDeploy() {
  if (!canDeploy.value || !result.value) return
  deploying.value = true
  logLines.value = []
  deployFailed.value = false
  lastDeploy.value = null
  try {
    const resp = await deployBlueprint({
      blueprint: result.value.blueprint,
      target: target.value,
      node_id: target.value === 'local_node' ? nodeId.value : null,
      region: region.value.trim() || null,
    })
    lastDeploy.value = resp
    for (const line of resp.logs) {
      const tone = /error|failed|not found/i.test(line) ? 'danger' : /ok|complete/i.test(line) ? 'ok' : 'info'
      logLines.value.push({ text: line, tone })
    }
    logLines.value.push({
      text: resp.ok ? t('hybrid.ai.deploySuccess') : t('hybrid.ai.deployFailed'),
      tone: resp.ok ? 'ok' : 'danger',
    })
    deployFailed.value = !resp.ok
    if (resp.ok) toast.success(t('hybrid.ai.deploySuccess'))
    else toast.warning(t('hybrid.ai.deployFailed'))
    await refreshNodes().catch(() => {})
  } catch (err) {
    const message = toastError(err, 'Deploy failed')
    logLines.value.push({ text: message, tone: 'danger' })
    deployFailed.value = true
    toast.error(message)
  } finally {
    deploying.value = false
  }
}

function primaryViewLabel(deploy: BlueprintDeployResponse): string {
  if (deploy.mode === 'iac') return t('hybrid.ai.viewWorkspace')
  return t('hybrid.ai.viewNode')
}
</script>

<template>
  <section class="lp-panel space-y-5 p-5 sm:p-6">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0 space-y-1">
        <p class="lp-label">{{ t('hybrid.ai.title') }}</p>
        <p class="text-sm leading-relaxed text-[var(--lp-muted)]">{{ t('hybrid.ai.subtitle') }}</p>
      </div>
      <span
        class="shrink-0 rounded-md px-2.5 py-1 text-xs font-medium"
        :class="providerStatus?.gemini_configured
          ? 'bg-[var(--lp-ok)]/15 text-[var(--lp-ok)]'
          : 'bg-[var(--lp-warn)]/15 text-[var(--lp-warn)]'"
      >
        {{ providerStatus?.gemini_configured ? t('hybrid.ai.geminiOn') : t('hybrid.ai.geminiOff') }}
      </span>
    </header>

    <label class="block">
      <span class="lp-label mb-1.5 block">{{ t('hybrid.ai.promptLabel') }}</span>
      <textarea
        v-model="prompt"
        rows="3"
        class="lp-input w-full resize-y"
        :placeholder="t('hybrid.ai.promptPlaceholder')"
      />
    </label>

    <div class="grid gap-3 sm:grid-cols-3">
      <label class="block">
        <span class="lp-label mb-1.5 block">{{ t('hybrid.ai.target') }}</span>
        <select v-model="target" class="lp-input w-full">
          <option v-for="opt in targets" :key="opt.value" :value="opt.value">
            {{ t(opt.labelKey) }}
          </option>
        </select>
      </label>

      <label v-if="target === 'local_node'" class="block">
        <span class="lp-label mb-1.5 block">{{ t('hybrid.ai.nodeLabel') }}</span>
        <select v-model="nodeId" class="lp-input w-full">
          <option :value="null">{{ t('hybrid.ai.selectNode') }}</option>
          <option v-for="n in onlineNodes" :key="n.id" :value="n.id">{{ n.name }}</option>
        </select>
      </label>

      <label v-else class="block">
        <span class="lp-label mb-1.5 block">{{ t('hybrid.ai.region') }}</span>
        <input v-model="region" class="lp-input w-full" placeholder="us-central1">
      </label>
    </div>

    <p v-if="target === 'local_node' && !nodeId" class="text-xs text-[var(--lp-warn)]">
      {{ t('hybrid.ai.needNode') }}
    </p>

    <div class="flex flex-wrap gap-2">
      <button type="button" class="lp-btn-primary" :disabled="!canGenerate" @click="onGenerate">
        {{ generating ? t('hybrid.ai.generating') : t('hybrid.ai.generate') }}
      </button>
      <button type="button" class="lp-btn-ghost" :disabled="!canDeploy" @click="onDeploy">
        {{ deploying ? t('hybrid.ai.deploying') : t('hybrid.ai.deploy') }}
      </button>
    </div>

    <p v-if="!result" class="rounded-lg border border-dashed border-[var(--lp-line)] px-4 py-6 text-center text-sm text-[var(--lp-muted)]">
      {{ t('hybrid.ai.noBlueprint') }}
    </p>

    <div v-if="result" class="space-y-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="lp-label">{{ t('hybrid.ai.blueprint') }}: {{ result.blueprint.name }}</p>
        <span class="text-xs text-[var(--lp-muted)]">
          {{ result.source === 'gemini' ? t('hybrid.ai.sourceGemini') : t('hybrid.ai.sourceHeuristic') }}
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <span
          class="rounded-md px-2.5 py-1 text-xs font-medium"
          :class="result.validation.valid
            ? 'bg-[var(--lp-ok)]/15 text-[var(--lp-ok)]'
            : 'bg-[var(--lp-danger)]/15 text-[var(--lp-danger)]'"
        >
          {{ result.validation.valid ? t('hybrid.ai.valid') : t('hybrid.ai.invalid') }}
        </span>
        <span
          v-if="result.validation.adjusted"
          class="rounded-md bg-[var(--lp-warn)]/15 px-2.5 py-1 text-xs font-medium text-[var(--lp-warn)]"
        >
          {{ t('hybrid.ai.adjusted') }}
        </span>
      </div>
      <ul v-if="result.validation.violations.length" class="space-y-1 text-xs">
        <li
          v-for="(v, i) in result.validation.violations"
          :key="i"
          :class="v.severity === 'error' ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-warn)]'"
        >
          - {{ v.message }}
        </li>
      </ul>

      <div>
        <p class="lp-label mb-2">{{ t('hybrid.ai.services') }}</p>
        <div class="overflow-hidden rounded-lg border border-[var(--lp-line)]">
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm">
              <thead class="bg-[var(--lp-panel-2)] text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">
                <tr>
                  <th class="px-3 py-2">name</th>
                  <th class="px-3 py-2">image</th>
                  <th class="px-3 py-2">kind</th>
                  <th class="px-3 py-2">cpu</th>
                  <th class="px-3 py-2">mem</th>
                  <th class="px-3 py-2">ports</th>
                </tr>
              </thead>
              <tbody class="text-[var(--lp-text)]">
                <tr
                  v-for="svc in result.blueprint.services"
                  :key="svc.name"
                  class="border-t border-[var(--lp-line)]"
                >
                  <td class="px-3 py-2.5 font-medium">{{ svc.name }}</td>
                  <td class="px-3 py-2.5 font-mono text-xs">{{ svc.image }}</td>
                  <td class="px-3 py-2.5">{{ svc.kind }}</td>
                  <td class="px-3 py-2.5">{{ svc.cpu_limit }}</td>
                  <td class="px-3 py-2.5">{{ svc.memory_mb }}MB</td>
                  <td class="px-3 py-2.5 font-mono text-xs">
                    {{ svc.ports.map((p) => `${p.host_port}:${p.container_port}`).join(', ') || '-' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/70 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <p class="lp-label">{{ t('hybrid.ai.cost') }}</p>
          <div class="flex flex-wrap gap-4 text-sm">
            <span class="text-[var(--lp-text)]">
              {{ t('hybrid.ai.hourly') }}: <strong>{{ money(result.cost.hourly_usd) }}</strong>
            </span>
            <span class="text-[var(--lp-text)]">
              {{ t('hybrid.ai.monthly') }}: <strong>{{ money(result.cost.monthly_usd) }}</strong>
            </span>
          </div>
        </div>
        <p v-if="result.cost.self_hosted" class="mt-1.5 text-xs text-[var(--lp-muted)]">
          {{ t('hybrid.ai.selfHosted') }}
        </p>
      </div>

      <div v-if="logLines.length" class="space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="lp-label">{{ t('hybrid.ai.deployLog') }}</p>
          <button
            v-if="canFix"
            type="button"
            class="lp-btn-primary text-xs"
            :disabled="fixing"
            @click="onFixWithAi"
          >
            {{ fixing ? t('hybrid.ai.fixing') : t('hybrid.ai.fixWithAi') }}
          </button>
        </div>
        <div class="lp-console max-h-72 overflow-y-auto rounded-lg">
          <div
            v-for="(line, i) in logLines"
            :key="i"
            class="lp-console-line"
            :class="{
              'lp-console-line-ok': line.tone === 'ok',
              'lp-console-line-warn': line.tone === 'warn',
              'lp-console-line-danger': line.tone === 'danger',
              'lp-console-line-info': line.tone === 'info',
            }"
          >
            {{ line.text }}
          </div>
        </div>
        <p v-if="deployFailed" class="text-xs text-[var(--lp-muted)]">
          {{ t('hybrid.ai.fixHint') }}
        </p>
      </div>

      <div
        v-if="showDeployResult && lastDeploy"
        class="space-y-3 rounded-xl border border-[var(--lp-ok)]/35 bg-[var(--lp-ok)]/10 p-4"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p class="lp-label text-[var(--lp-ok)]">{{ t('hybrid.ai.nextSteps') }}</p>
            <p v-if="lastDeploy.node_name" class="mt-1 text-sm text-[var(--lp-text)]">
              {{ t('hybrid.ai.deployedOnNode', { name: lastDeploy.node_name }) }}
            </p>
            <p v-else-if="lastDeploy.workspace_id" class="mt-1 text-sm text-[var(--lp-text)]">
              {{ t('hybrid.ai.viewWorkspace') }}
            </p>
          </div>
          <button type="button" class="lp-btn-ghost text-xs" @click="lastDeploy = null">
            {{ t('hybrid.ai.dismissResult') }}
          </button>
        </div>

        <div class="flex flex-wrap gap-2">
          <NuxtLink
            v-if="lastDeploy.view_path"
            :to="lastDeploy.view_path"
            class="lp-btn-primary text-xs"
          >
            {{ primaryViewLabel(lastDeploy) }}
          </NuxtLink>
          <a
            v-for="svc in lastDeploy.services.filter((s) => s.ok && s.url)"
            :key="svc.container_name"
            :href="svc.url!"
            target="_blank"
            rel="noopener noreferrer"
            class="lp-btn-ghost text-xs"
          >
            {{ t('hybrid.ai.openService', { name: svc.name }) }}
          </a>
        </div>

        <div v-if="lastDeploy.services.length" class="space-y-1">
          <p class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">
            {{ t('hybrid.ai.deployedServices') }}
          </p>
          <ul class="space-y-1 text-sm">
            <li
              v-for="svc in lastDeploy.services"
              :key="`list-${svc.container_name}`"
              class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs"
            >
              <span :class="svc.ok ? 'text-[var(--lp-text)]' : 'text-[var(--lp-danger)]'">
                {{ svc.container_name }}
              </span>
              <a
                v-if="svc.url"
                :href="svc.url"
                target="_blank"
                rel="noopener noreferrer"
                class="text-[var(--lp-accent)] hover:underline"
              >
                {{ svc.url }}
              </a>
              <span v-else class="text-[var(--lp-muted)]">{{ t('hybrid.ai.noServiceUrl') }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>
