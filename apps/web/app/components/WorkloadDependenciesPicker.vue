<script setup lang="ts">
import type {
  CloudProvider,
  DataStoreKind,
  DataStoreDependency,
  DependencyPlacement,
  MessageBrokerKind,
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

function setStoreUrl(key: DataStoreKind, url: string) {
  dependencies.value = {
    ...dependencies.value,
    [key]: { ...dependencies.value[key], connection_url: url || null },
  }
}

function storeUrlPlaceholder(key: DataStoreKind): string {
  switch (key) {
    case 'postgres':
      return 'postgresql://user:pass@host:5432/db'
    case 'mysql':
      return 'mysql://user:pass@host:3306/db'
    case 'mongodb':
      return 'mongodb+srv://user:pass@host/db'
    case 'redis':
      return 'redis://host:6379/0'
    default:
      return ''
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

// --- Message brokers (Kafka / RabbitMQ): in-cluster or bring-your-own URL ---

const brokers: Array<{ key: MessageBrokerKind; title: string; hint: string }> = [
  { key: 'kafka', title: 'Apache Kafka', hint: 'Event streaming / pub-sub bus' },
  { key: 'rabbitmq', title: 'RabbitMQ', hint: 'AMQP message broker' },
]

function brokerRef(key: MessageBrokerKind): DataStoreDependency {
  return dependencies.value[key]
}

function toggleBroker(key: MessageBrokerKind, enabled: boolean) {
  const current = brokerRef(key)
  dependencies.value = {
    ...dependencies.value,
    [key]: { ...current, enabled, placement: enabled ? current.placement || 'in_cluster' : current.placement },
  }
}

function setBrokerPlacement(key: MessageBrokerKind, placement: DependencyPlacement) {
  dependencies.value = {
    ...dependencies.value,
    [key]: { ...dependencies.value[key], placement },
  }
}

function setBrokerUrl(key: MessageBrokerKind, url: string) {
  dependencies.value = {
    ...dependencies.value,
    [key]: { ...dependencies.value[key], connection_url: url || null },
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
          <button
            type="button"
            class="rounded-lg border px-3 py-1.5 text-xs transition"
            :class="
              storeRef(store.key).placement === 'external'
                ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
                : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
            "
            :disabled="disabled"
            @click="setPlacement(store.key, 'external')"
          >
            {{ t('scaffold.dependencies.bringYourOwn') }}
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
          <label v-if="storeRef(store.key).placement === 'external'" class="block w-full space-y-1">
            <span class="lp-label">{{ t('scaffold.dependencies.connectionUrl') }}</span>
            <input
              :value="storeRef(store.key).connection_url || ''"
              type="text"
              class="lp-input font-mono text-xs"
              :placeholder="storeUrlPlaceholder(store.key)"
              :disabled="disabled"
              @input="setStoreUrl(store.key, ($event.target as HTMLInputElement).value)"
            >
          </label>
          <p
            v-else-if="storeRef(store.key).placement === 'in_cluster' && storeRef(store.key).enabled"
            class="flex items-start gap-1.5 text-xs text-[var(--lp-muted)]"
          >
            <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">bolt</span>
            <span>{{ t('scaffold.dependencies.inClusterAutoInjected') }}</span>
          </p>
        </div>
      </div>
    </div>

    <!-- Message brokers: in-cluster or bring-your-own (external) URL. -->
    <div class="space-y-2">
      <p class="text-sm font-medium">{{ t('scaffold.dependencies.brokersTitle') }}</p>
      <p class="text-xs text-[var(--lp-muted)]">{{ t('scaffold.dependencies.brokersBlurb') }}</p>
      <div class="space-y-3">
        <div
          v-for="broker in brokers"
          :key="broker.key"
          class="rounded-lg border border-[var(--lp-line)] p-3"
        >
          <label class="flex cursor-pointer items-start justify-between gap-3">
            <span>
              <span class="block text-sm font-medium">{{ broker.title }}</span>
              <span class="block text-xs text-[var(--lp-muted)]">{{ broker.hint }}</span>
            </span>
            <input
              :checked="brokerRef(broker.key).enabled"
              type="checkbox"
              class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
              :disabled="disabled"
              @change="toggleBroker(broker.key, ($event.target as HTMLInputElement).checked)"
            >
          </label>

          <div
            v-if="brokerRef(broker.key).enabled"
            class="mt-3 space-y-3 border-t border-[var(--lp-line)] pt-3"
          >
            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded-lg border px-3 py-1.5 text-xs transition"
                :class="
                  brokerRef(broker.key).placement !== 'external'
                    ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
                    : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
                "
                :disabled="disabled"
                @click="setBrokerPlacement(broker.key, 'in_cluster')"
              >
                {{ t('scaffold.dependencies.inCluster') }}
              </button>
              <button
                type="button"
                class="rounded-lg border px-3 py-1.5 text-xs transition"
                :class="
                  brokerRef(broker.key).placement === 'external'
                    ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
                    : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
                "
                :disabled="disabled"
                @click="setBrokerPlacement(broker.key, 'external')"
              >
                {{ t('scaffold.dependencies.bringYourOwn') }}
              </button>
            </div>
            <label v-if="brokerRef(broker.key).placement === 'external'" class="block space-y-1">
              <span class="lp-label">{{ t('scaffold.dependencies.brokerUrl') }}</span>
              <input
                :value="brokerRef(broker.key).connection_url || ''"
                type="text"
                class="lp-input font-mono text-xs"
                :placeholder="broker.key === 'kafka' ? 'broker1:9092,broker2:9092' : 'amqps://user:pass@host:5671/vhost'"
                :disabled="disabled"
                @input="setBrokerUrl(broker.key, ($event.target as HTMLInputElement).value)"
              >
            </label>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
