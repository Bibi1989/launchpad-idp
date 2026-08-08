<script setup lang="ts">
import type {
  CloudProvider,
  DataStoreKind,
  DataStoreDependency,
  DependencyPlacement,
  WorkloadDependenciesConfig,
} from '~/types/provisioning'

const dependencies = defineModel<WorkloadDependenciesConfig>('dependencies', { required: true })

const { t } = useI18n()

const props = defineProps<{
  provider: CloudProvider
  gcpCloudSql?: boolean
  gcpCloudSqlEngine?: 'postgres' | 'mysql' | 'mariadb'
  gcpMemorystore?: boolean
  gcpMemorystoreEngine?: 'redis' | 'memcached'
  awsRds?: boolean
  awsRdsEngine?: 'postgres' | 'mysql' | 'mariadb'
  awsElasticache?: boolean
  awsElasticacheEngine?: 'redis' | 'memcached'
  azureCosmosDb?: boolean
  azureCosmosApi?: 'mongodb' | 'sql'
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
  if (key === 'postgres') {
    if (props.provider === 'gcp') {
      return Boolean(props.gcpCloudSql) && (props.gcpCloudSqlEngine ?? 'postgres') === 'postgres'
    }
    if (props.provider === 'aws') {
      return Boolean(props.awsRds) && (props.awsRdsEngine ?? 'postgres') === 'postgres'
    }
    return false
  }
  if (key === 'mysql') {
    if (props.provider === 'gcp') {
      return Boolean(props.gcpCloudSql) && props.gcpCloudSqlEngine === 'mysql'
    }
    if (props.provider === 'aws') {
      return Boolean(props.awsRds) && props.awsRdsEngine === 'mysql'
    }
    return false
  }
  if (key === 'mongodb') {
    return (
      props.provider === 'azure' &&
      Boolean(props.azureCosmosDb) &&
      (props.azureCosmosApi ?? 'mongodb') === 'mongodb'
    )
  }
  if (key === 'redis') {
    if (props.provider === 'gcp') {
      return (
        Boolean(props.gcpMemorystore) && (props.gcpMemorystoreEngine ?? 'redis') === 'redis'
      )
    }
    if (props.provider === 'aws') {
      return (
        Boolean(props.awsElasticache) && (props.awsElasticacheEngine ?? 'redis') === 'redis'
      )
    }
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
      <p class="text-sm font-medium">{{ t('scaffold.dependencies.title') }}</p>
      <p class="mt-1 text-xs text-[var(--lp-muted)]">
        {{ t('scaffold.dependencies.blurb') }}
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
            {{ t('scaffold.dependencies.inCluster') }}
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
            {{ t('scaffold.dependencies.managedCloud') }}
          </button>
          <p
            v-if="store.key !== 'redis' && storeRef(store.key).placement === 'managed' && !managedAvailable(store.key)"
            class="text-xs text-amber-400"
          >
            {{ t('scaffold.dependencies.enableManagedFirst', { service: store.managedLabel }) }}
          </p>
          <p
            v-else-if="store.key === 'redis' && storeRef(store.key).placement === 'managed' && !managedAvailable(store.key)"
            class="text-xs text-amber-400"
          >
            {{ t('scaffold.dependencies.enableManagedFirst', { service: store.managedLabel }) }}
          </p>
          <p
            v-else-if="storeRef(store.key).placement === 'managed'"
            class="text-xs text-[var(--lp-muted)]"
          >
            {{ t('scaffold.dependencies.managedHint') }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
