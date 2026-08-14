<script setup lang="ts">
import type { IngressClassName, KubernetesPackaging, KubernetesWorkloadOptions } from '~/types/provisioning'
import { defaultImageSecurityScanConfig } from '~/utils/cloudValidation'

const packaging = defineModel<KubernetesPackaging>('packaging', { required: true })
const options = defineModel<KubernetesWorkloadOptions>('options', { required: true })

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    /** When false, hide the "None" packaging option (e.g. Dev kind always needs manifests). */
    allowNone?: boolean
    cloudProvider?: string | null
  }>(),
  { allowNone: true },
)

if (!options.value.image_scan) {
  options.value.image_scan = defaultImageSecurityScanConfig()
}

const packagingChoices = computed(() => {
  const all = [
    { value: 'none' as const, title: t('k8s.packaging.none'), desc: t('k8s.packaging.noneDesc') },
    { value: 'raw_manifests' as const, title: t('k8s.packaging.rawManifests'), desc: t('k8s.packaging.rawManifestsDesc') },
    { value: 'helm' as const, title: t('k8s.packaging.helm'), desc: t('k8s.packaging.helmDesc') },
    { value: 'kustomize' as const, title: t('k8s.packaging.kustomize'), desc: t('k8s.packaging.kustomizeDesc') },
  ]
  return props.allowNone ? all : all.filter((item) => item.value !== 'none')
})

const workloadToggles: Array<{
  key: keyof Omit<KubernetesWorkloadOptions, 'ingress_class'>
  title: string
  hint: string
  group: 'workloads' | 'config' | 'policy'
}> = [
  { key: 'deployment', title: 'Deployment', hint: 'Stateless replicas + rolling updates', group: 'workloads' },
  { key: 'service', title: 'Service', hint: 'Stable ClusterIP endpoint', group: 'workloads' },
  { key: 'pod', title: 'Pod', hint: 'Single pod (prefer Deployment in prod)', group: 'workloads' },
  { key: 'statefulset', title: 'StatefulSet', hint: 'Ordered pods + stable identity', group: 'workloads' },
  { key: 'daemonset', title: 'DaemonSet', hint: 'One pod per node', group: 'workloads' },
  { key: 'job', title: 'Job', hint: 'Run-to-completion batch task', group: 'workloads' },
  { key: 'cronjob', title: 'CronJob', hint: 'Scheduled Jobs', group: 'workloads' },
  { key: 'ingress', title: 'Ingress', hint: 'HTTP route + ingress class', group: 'workloads' },
  { key: 'install_ingress_nginx', title: 'Install ingress-nginx', hint: 'Helm addon values file', group: 'workloads' },
  { key: 'service_account', title: 'ServiceAccount', hint: 'Dedicated SA for the workload', group: 'config' },
  { key: 'config_map', title: 'ConfigMap', hint: 'Non-secret env via envFrom', group: 'config' },
  { key: 'secret', title: 'Secret', hint: 'Opaque secrets via envFrom', group: 'config' },
  { key: 'pvc', title: 'PersistentVolumeClaim', hint: 'Request durable storage', group: 'config' },
  { key: 'role', title: 'Role', hint: 'Namespaced RBAC permissions', group: 'config' },
  { key: 'role_binding', title: 'RoleBinding', hint: 'Bind Role to ServiceAccount', group: 'config' },
  { key: 'hpa', title: 'HPA', hint: 'CPU/memory HorizontalPodAutoscaler', group: 'policy' },
  { key: 'vpa', title: 'VPA', hint: 'VerticalPodAutoscaler (needs CRDs)', group: 'policy' },
  { key: 'pdb', title: 'PodDisruptionBudget', hint: 'minAvailable during drains', group: 'policy' },
  { key: 'network_policy', title: 'NetworkPolicy', hint: 'Zero-trust ingress/egress', group: 'policy' },
  { key: 'resource_quota', title: 'ResourceQuota', hint: 'Namespace hard limits', group: 'policy' },
  { key: 'limit_range', title: 'LimitRange', hint: 'Default container limits', group: 'policy' },
]

const groups = computed(() => [
  { id: 'workloads' as const, title: t('k8s.packaging.groups.workloads') },
  { id: 'config' as const, title: t('k8s.packaging.groups.config') },
  { id: 'policy' as const, title: t('k8s.packaging.groups.policy') },
])

const ingressClasses: Array<{ value: IngressClassName; label: string }> = [
  { value: 'nginx', label: 'nginx' },
  { value: 'traefik', label: 'traefik' },
  { value: 'gce', label: 'gce (GKE)' },
  { value: 'alb', label: 'alb (AWS)' },
  { value: 'azure-application-gateway', label: 'azure-application-gateway' },
  { value: 'contour', label: 'contour' },
]

watch(
  () => options.value.install_ingress_nginx,
  (enabled) => {
    if (enabled) {
      options.value.ingress = true
      options.value.ingress_class = 'nginx'
    }
  },
)

watch(
  () => options.value.ingress,
  (enabled) => {
    if (!enabled) {
      options.value.install_ingress_nginx = false
    }
  },
)
</script>

<template>
  <div class="space-y-4 rounded-xl border border-[var(--lp-line)] p-4">
    <div>
      <p class="text-sm font-medium">{{ t('k8s.packaging.title') }}</p>
      <p class="mt-1 text-xs text-[var(--lp-muted)]">
        {{ t('k8s.packaging.blurb') }}
      </p>
    </div>

    <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      <label
        v-for="opt in packagingChoices"
        :key="opt.value"
        class="flex cursor-pointer flex-col rounded-lg border p-3 transition"
        :class="
          packaging === opt.value
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
            : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
        "
      >
        <input
          v-model="packaging"
          type="radio"
          class="sr-only"
          name="kubernetes_packaging"
          :value="opt.value"
        >
        <p class="text-sm font-medium leading-snug">{{ opt.title }}</p>
        <p class="mt-1 break-all font-mono text-[11px] leading-snug text-[var(--lp-muted)]">
          {{ opt.desc }}
        </p>
      </label>
    </div>

    <div v-if="packaging !== 'none'" class="space-y-5 border-t border-[var(--lp-line)] pt-4">
      <div>
        <p class="text-sm font-medium">{{ t('k8s.packaging.objectsTitle') }}</p>
        <p class="mt-1 text-xs text-[var(--lp-muted)]">
          {{ t('k8s.packaging.objectsBlurb') }}
        </p>
      </div>

      <div v-for="group in groups" :key="group.id" class="space-y-2">
        <p class="lp-label">{{ group.title }}</p>
        <div class="grid gap-2 sm:grid-cols-2">
          <label
            v-for="toggle in workloadToggles.filter((t) => t.group === group.id)"
            :key="toggle.key"
            class="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--lp-line)] p-3"
            :class="{
              'opacity-50': toggle.key === 'install_ingress_nginx' && !options.ingress,
            }"
          >
            <input
              v-model="options[toggle.key]"
              type="checkbox"
              class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
              :disabled="toggle.key === 'install_ingress_nginx' && !options.ingress"
            >
            <span>
              <span class="block text-sm font-medium">{{ toggle.title }}</span>
              <span class="block text-xs text-[var(--lp-muted)]">{{ toggle.hint }}</span>
            </span>
          </label>
        </div>
      </div>

      <label v-if="options.ingress" class="block space-y-2">
        <span class="lp-label">{{ t('k8s.packaging.ingressClass') }}</span>
        <select v-model="options.ingress_class" class="lp-input">
          <option v-for="cls in ingressClasses" :key="cls.value" :value="cls.value">
            {{ cls.label }}
          </option>
        </select>
      </label>

      <KubernetesImageSourcePicker
        v-model:source="options.image_source"
        v-model:image-scan="options.image_scan"
        :cloud-provider="props.cloudProvider"
      />
    </div>
  </div>
</template>
