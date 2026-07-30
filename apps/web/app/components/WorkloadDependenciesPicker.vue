<script setup lang="ts">
import type {
  CloudProvider,
  DataStoreKind,
  DataStoreDependency,
  DependencyPlacement,
  WorkloadDependenciesConfig,
} from '~/types/provisioning'

const dependencies = defineModel<WorkloadDependenciesConfig>('dependencies', { required: true })

const props = defineProps<{
  provider: CloudProvider
  gcpCloudSql?: boolean
  gcpMemorystore?: boolean
  awsRds?: boolean
  awsElasticache?: boolean
  azureCosmosDb?: boolean
  azureRedisCache?: boolean
  disabled?: boolean
}>()

const stores: Array<{
  key: DataStoreKind
  title: string
  hint: string
  managedLabel: string
}> = [
  {
    key: 'postgres',
    title: 'PostgreSQL',
    hint: 'Relational DB for app persistence',
    managedLabel: 'Cloud SQL / RDS',
  },
  {
    key: 'mysql',
    title: 'MySQL',
    hint: 'Relational DB (MySQL-compatible)',
    managedLabel: 'Cloud SQL / RDS',
  },
  {
    key: 'mongodb',
    title: 'MongoDB',
    hint: 'Document store',
    managedLabel: 'Cosmos DB (Azure)',
  },
  {
    key: 'redis',
    title: 'Redis',
    hint: 'Cache, queues, and session store',
    managedLabel: 'Memorystore / ElastiCache / Azure Redis',
  },
]

function storeRef(key: DataStoreKind): DataStoreDependency {
  return dependencies.value[key]
}

function managedAvailable(key: DataStoreKind): boolean {
  if (props.provider === 'local') return false
  if (key === 'postgres' || key === 'mysql') {
    if (props.provider === 'gcp') return Boolean(props.gcpCloudSql)
    if (props.provider === 'aws') return Boolean(props.awsRds)
    return false
  }
  if (key === 'mongodb') {
    return props.provider === 'azure' && Boolean(props.azureCosmosDb)
  }
  if (key === 'redis') {
    if (props.provider === 'gcp') return Boolean(props.gcpMemorystore)
    if (props.provider === 'aws') return Boolean(props.awsElasticache)
    if (props.provider === 'azure') return Boolean(props.azureRedisCache)
    return false
  }
  return false
}

function setPlacement(key: DataStoreKind, placement: DependencyPlacement) {
  dependencies.value = {
    ...dependencies.value,
    [key]: { ...dependencies.value[key], placement },
  }
}

function toggleStore(key: DataStoreKind, enabled: boolean) {
  const current = storeRef(key)
  dependencies.value = {
    ...dependencies.value,
    [key]: {
      ...current,
      enabled,
      placement: enabled && !managedAvailable(key) ? 'in_cluster' : current.placement,
    },
  }
}
</script>

<template>
  <div class="space-y-4 rounded-xl border border-[var(--lp-line)] p-4">
    <div>
      <p class="text-sm font-medium">Workload dependencies</p>
      <p class="mt-1 text-xs text-[var(--lp-muted)]">
        Add databases and caches as companion services in the cluster, or wire managed cloud
        resources when enabled above.
      </p>
    </div>

    <div class="space-y-3">
      <div
        v-for="store in stores"
        :key="store.key"
        class="rounded-lg border border-[var(--lp-line)] p-3"
      >
        <label class="flex cursor-pointer items-start justify-between gap-3">
          <span>
            <span class="block text-sm font-medium">{{ store.title }}</span>
            <span class="block text-xs text-[var(--lp-muted)]">{{ store.hint }}</span>
          </span>
          <input
            :checked="storeRef(store.key).enabled"
            type="checkbox"
            class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
            :disabled="disabled"
            @change="toggleStore(store.key, ($event.target as HTMLInputElement).checked)"
          >
        </label>

        <div
          v-if="storeRef(store.key).enabled"
          class="mt-3 flex flex-wrap gap-2 border-t border-[var(--lp-line)] pt-3"
        >
          <button
            type="button"
            class="rounded-lg border px-3 py-1.5 text-xs transition"
            :class="
              storeRef(store.key).placement === 'in_cluster'
                ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
                : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
            "
            :disabled="disabled"
            @click="setPlacement(store.key, 'in_cluster')"
          >
            In-cluster
          </button>
          <button
            type="button"
            class="rounded-lg border px-3 py-1.5 text-xs transition"
            :class="
              storeRef(store.key).placement === 'managed'
                ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
                : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
            "
            :disabled="disabled || !managedAvailable(store.key)"
            @click="setPlacement(store.key, 'managed')"
          >
            Managed cloud
          </button>
          <p
            v-if="store.key !== 'redis' && storeRef(store.key).placement === 'managed' && !managedAvailable(store.key)"
            class="text-xs text-amber-400"
          >
            Enable {{ store.managedLabel }} in cloud resources first.
          </p>
          <p
            v-else-if="store.key === 'redis' && storeRef(store.key).placement === 'managed' && !managedAvailable(store.key)"
            class="text-xs text-amber-400"
          >
            Enable {{ store.managedLabel }} in cloud resources first.
          </p>
          <p
            v-else-if="storeRef(store.key).placement === 'managed'"
            class="text-xs text-[var(--lp-muted)]"
          >
            Connection strings use Terraform outputs — see infra/MANAGED_DATASTORES.md after apply.
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
