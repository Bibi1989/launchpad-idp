<script setup lang="ts">
import type { K8sDescribeMetadata } from '~/types/k8s'

const props = defineProps<{
  open: boolean
  metadata: K8sDescribeMetadata | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const activeTab = ref<'yaml' | 'events'>('yaml')

function copyYaml() {
  if (props.metadata?.manifest_yaml) {
    navigator.clipboard.writeText(props.metadata.manifest_yaml)
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm transition-opacity"
      @click.self="emit('close')"
    >
      <div class="flex h-full w-full max-w-2xl flex-col bg-[var(--lp-panel)] border-l border-[var(--lp-line)] shadow-2xl animate-slide-left">
        <!-- Drawer Header -->
        <div class="flex items-center justify-between border-b border-[var(--lp-line)] px-6 py-4">
          <div class="flex items-center gap-3">
            <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--lp-accent)]/10 text-[var(--lp-accent)]">
              <span class="material-symbols-outlined text-xl">description</span>
            </div>
            <div>
              <h3 class="font-mono text-base font-semibold text-[var(--lp-text)]">
                {{ metadata?.kind }} / {{ metadata?.name }}
              </h3>
              <p class="font-mono text-xs text-[var(--lp-muted)]">
                Namespace: {{ metadata?.namespace || 'default' }}
              </p>
            </div>
          </div>
          <button
            type="button"
            class="rounded-lg p-2 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            @click="emit('close')"
          >
            <span class="material-symbols-outlined text-xl">close</span>
          </button>
        </div>

        <!-- Drawer Content Tabs -->
        <div class="flex items-center justify-between border-b border-[var(--lp-line)] px-6 py-2 bg-[var(--lp-panel-2)]/40 font-mono text-xs">
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="rounded-md px-3 py-1.5 font-semibold transition"
              :class="activeTab === 'yaml' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
              @click="activeTab = 'yaml'"
            >
              YAML Definition
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 font-semibold transition"
              :class="activeTab === 'events' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
              @click="activeTab = 'events'"
            >
              Events History ({{ metadata?.events.length || 0 }})
            </button>
          </div>
          <button
            v-if="activeTab === 'yaml'"
            type="button"
            class="flex items-center gap-1 text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
            @click="copyYaml"
          >
            <span class="material-symbols-outlined text-sm">content_copy</span>
            Copy YAML
          </button>
        </div>

        <!-- Body Container -->
        <div class="flex-1 overflow-y-auto p-6 font-mono text-xs">
          <div v-if="loading" class="flex h-40 items-center justify-center text-[var(--lp-muted)]">
            <span class="material-symbols-outlined animate-spin text-2xl mr-2">sync</span>
            Fetching kubectl describe metadata…
          </div>

          <!-- YAML View -->
          <div v-else-if="activeTab === 'yaml'" class="rounded-xl border border-[var(--lp-line)] bg-black/80 p-4 text-emerald-300 font-mono overflow-x-auto shadow-inner leading-relaxed">
            <pre>{{ metadata?.manifest_yaml || '# No YAML definition found' }}</pre>
          </div>

          <!-- Events Table -->
          <div v-else-if="activeTab === 'events'" class="space-y-3">
            <div v-if="!metadata?.events.length" class="text-center text-[var(--lp-muted)] py-8">
              No recent Kubernetes control plane events recorded.
            </div>
            <table v-else class="w-full text-left border-collapse font-mono text-xs">
              <thead>
                <tr class="border-b border-[var(--lp-line)] text-[var(--lp-muted)]">
                  <th class="py-2">Type</th>
                  <th class="py-2">Reason</th>
                  <th class="py-2">Message</th>
                  <th class="py-2 text-right">Age</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-[var(--lp-line)]/50">
                <tr v-for="(evt, idx) in metadata?.events" :key="idx" class="hover:bg-[var(--lp-panel-2)]/30">
                  <td class="py-2 font-semibold" :class="evt.type === 'Warning' ? 'text-rose-400' : 'text-emerald-400'">
                    {{ evt.type }}
                  </td>
                  <td class="py-2 text-[var(--lp-accent)] font-semibold">{{ evt.reason }}</td>
                  <td class="py-2 text-[var(--lp-text)] max-w-xs truncate" :title="evt.message">{{ evt.message }}</td>
                  <td class="py-2 text-right text-[var(--lp-muted)]">{{ evt.age }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
