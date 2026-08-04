<script setup lang="ts">
import { environmentCreateSchema } from '~/utils/validation'
import type { WorkspaceListItem } from '~/types/provisioning'

const emit = defineEmits<{
  created: [environmentId: string]
}>()

const props = defineProps<{
  initialWorkspaceId?: string | null
}>()

const { create } = useEnvironments()
const { listWorkspaces } = useProvisioning()
const route = useRoute()

const linkedFromQuery = computed(() => {
  const raw = route.query.workspace
  return typeof raw === 'string' && raw.length > 0 ? raw : null
})

const form = reactive<{
  name: string
  git_branch: string
  git_repo_url: string
  ttl_unit: 'hours' | 'minutes'
  ttl_value: number
  workspace_id: string | null
}>({
  name: '',
  git_branch: 'main',
  git_repo_url: 'https://github.com/example/app.git',
  ttl_unit: 'hours',
  ttl_value: 72,
  workspace_id: props.initialWorkspaceId ?? linkedFromQuery.value ?? null,
})

const workspaces = ref<WorkspaceListItem[]>([])
const fieldErrors = ref<Record<string, string>>({})
const submitting = ref(false)
const submitError = ref<string | null>(null)

onMounted(async () => {
  try {
    workspaces.value = await listWorkspaces()
  } catch {
    workspaces.value = []
  }
})

watch(linkedFromQuery, (id) => {
  if (id) form.workspace_id = id
})

async function onSubmit() {
  submitError.value = null
  fieldErrors.value = {}

  const parsed = environmentCreateSchema.safeParse({
    ...form,
    workspace_id: form.workspace_id || null,
  })
  if (!parsed.success) {
    for (const issue of parsed.error.issues) {
      const key = String(issue.path[0] ?? 'form')
      fieldErrors.value[key] = issue.message
    }
    return
  }

  submitting.value = true
  try {
    const result = await create(parsed.data)
    form.name = ''
    emit('created', result.id)
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : 'Failed to create environment'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <form class="space-y-5" @submit.prevent="onSubmit">
    <div class="grid gap-4 md:grid-cols-2">
      <label class="block space-y-2">
        <span class="lp-label">Name</span>
        <input
          v-model="form.name"
          class="lp-input"
          placeholder="demo-feature"
          autocomplete="off"
        >
        <p v-if="fieldErrors.name" class="text-sm text-[var(--lp-danger)]">{{ fieldErrors.name }}</p>
      </label>

      <label class="block space-y-2">
        <span class="lp-label">TTL</span>
        <div class="flex gap-2">
          <input
            v-model.number="form.ttl_value"
            type="number"
            min="1"
            :max="form.ttl_unit === 'minutes' ? 43200 : 720"
            class="lp-input flex-1"
          >
          <select v-model="form.ttl_unit" class="lp-input w-28">
            <option value="hours">Hours</option>
            <option value="minutes">Minutes</option>
          </select>
        </div>
        <input
          v-model.number="form.ttl_value"
          type="range"
          min="1"
          :max="form.ttl_unit === 'minutes' ? 240 : 168"
          class="mt-2 w-full accent-[var(--lp-accent)]"
        >
        <p v-if="fieldErrors.ttl_value" class="text-sm text-[var(--lp-danger)]">
          {{ fieldErrors.ttl_value }}
        </p>
      </label>

      <label class="block space-y-2 md:col-span-2">
        <span class="lp-label">Git repository URL</span>
        <div class="flex items-center gap-3 border-b-2 border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-2 py-3 focus-within:border-[var(--lp-accent)]">
          <span class="material-symbols-outlined text-[var(--lp-muted)]">link</span>
          <input
            v-model="form.git_repo_url"
            class="w-full border-none bg-transparent font-mono text-sm outline-none"
            placeholder="https://github.com/org/repo.git"
          >
        </div>
        <p v-if="fieldErrors.git_repo_url" class="text-sm text-[var(--lp-danger)]">
          {{ fieldErrors.git_repo_url }}
        </p>
      </label>

      <label class="block space-y-2">
        <span class="lp-label">Git branch</span>
        <div class="flex items-center gap-3 border-b-2 border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-2 py-3 focus-within:border-[var(--lp-accent)]">
          <span class="material-symbols-outlined text-[var(--lp-muted)]">fork_right</span>
          <input
            v-model="form.git_branch"
            class="w-full border-none bg-transparent font-mono text-sm outline-none"
            placeholder="feature/my-change"
          >
        </div>
        <p v-if="fieldErrors.git_branch" class="text-sm text-[var(--lp-danger)]">
          {{ fieldErrors.git_branch }}
        </p>
      </label>

      <label class="block space-y-2">
        <span class="lp-label">Linked workspace</span>
        <select v-model="form.workspace_id" class="lp-input">
          <option :value="null">None</option>
          <option v-for="ws in workspaces" :key="ws.id" :value="ws.id">
            {{ ws.name }} ({{ ws.provider }}/{{ ws.engine }})
          </option>
        </select>
      </label>
    </div>

    <p v-if="submitError" class="text-sm text-[var(--lp-danger)]">{{ submitError }}</p>

    <button type="submit" class="lp-btn-primary w-full sm:w-auto" :disabled="submitting">
      <span class="material-symbols-outlined text-base">rocket_launch</span>
      {{ submitting ? 'Queuing…' : 'Launch environment' }}
    </button>
  </form>
</template>
