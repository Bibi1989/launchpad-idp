<script setup lang="ts">
import type { OrgMember, ProjectInvite, ProjectMember, ProjectSummary } from '~/types/auth'
import type { WorkspaceListItem } from '~/types/provisioning'
import { workspaceMetaTokens } from '~/utils/workspaceDisplay'

const { t } = useI18n()
const route = useRoute()
const {
  getProject,
  listMembers,
  updateMember,
  listInvites,
  createInvite,
  revokeInvite,
  renameProject,
} = useProjects()
const { listWorkspaces } = useProvisioning()
const { activeOrgId, listMembers: listOrgMembers } = useOrgs()
const { user } = useAuth()

const projectId = computed(() => String(route.params.id || ''))
const project = ref<ProjectSummary | null>(null)
const members = ref<ProjectMember[]>([])
const invites = ref<ProjectInvite[]>([])
const orgMembers = ref<OrgMember[]>([])
const workspaces = ref<WorkspaceListItem[]>([])
const workspacesLoading = ref(false)
const importOpen = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const inviting = ref(false)
const renaming = ref(false)
const editingName = ref(false)
const nameDraft = ref('')
const inviteForm = reactive({ email: '', role: 'member' })
const roleUpdatingId = ref<string | null>(null)

const canAdmin = computed(() => {
  const role = project.value?.role
  return role === 'owner' || role === 'admin'
})
const isOwner = computed(() => project.value?.role === 'owner')

const projectMemberEmails = computed(() => new Set(members.value.map((m) => m.email.toLowerCase())))

const inviteCandidates = computed(() =>
  orgMembers.value.filter((m) => !projectMemberEmails.value.has(m.email.toLowerCase())),
)

const workspacesHref = computed(() =>
  projectId.value
    ? `/workspaces?project_id=${encodeURIComponent(projectId.value)}`
    : '/workspaces',
)

const provisionHref = computed(() =>
  projectId.value
    ? `/provision?project_id=${encodeURIComponent(projectId.value)}`
    : '/provision',
)

async function loadWorkspaces() {
  if (!projectId.value) return
  workspacesLoading.value = true
  try {
    workspaces.value = await listWorkspaces({ projectId: projectId.value })
  } catch {
    workspaces.value = []
  } finally {
    workspacesLoading.value = false
  }
}

async function load() {
  if (!projectId.value) return
  loading.value = true
  error.value = null
  try {
    project.value = await getProject(projectId.value)
    nameDraft.value = project.value.name
    members.value = await listMembers(projectId.value)
    await loadWorkspaces()
    if (canAdmin.value) {
      invites.value = await listInvites(projectId.value)
      if (activeOrgId.value) {
        orgMembers.value = await listOrgMembers(activeOrgId.value)
      }
    } else {
      invites.value = []
      orgMembers.value = []
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.load')
  } finally {
    loading.value = false
  }
}

async function onRename() {
  if (!projectId.value || renaming.value || !nameDraft.value.trim()) return
  renaming.value = true
  error.value = null
  notice.value = null
  try {
    project.value = await renameProject(projectId.value, nameDraft.value.trim())
    editingName.value = false
    notice.value = t('projects.renamed')
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.rename')
  } finally {
    renaming.value = false
  }
}

async function onInvite() {
  if (!projectId.value || inviting.value || !inviteForm.email) return
  inviting.value = true
  notice.value = null
  error.value = null
  try {
    const created = await createInvite(projectId.value, {
      email: inviteForm.email,
      role: inviteForm.role,
    })
    inviteForm.email = ''
    await load()
    if (created.email_sent) {
      notice.value = t('projects.inviteEmailed', { email: created.email })
    } else if (created.email_error) {
      notice.value = t('projects.inviteEmailFailed', {
        email: created.email,
        error: created.email_error,
        url: created.invite_url || '-',
      })
    } else if (created.invite_url) {
      notice.value = t('projects.inviteCreatedSmtp', { url: created.invite_url })
    } else {
      notice.value = t('projects.inviteCreatedFor', { email: created.email })
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.invite')
  } finally {
    inviting.value = false
  }
}

async function onRevoke(inviteId: string) {
  if (!projectId.value) return
  try {
    await revokeInvite(projectId.value, inviteId)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.revoke')
  }
}

function canEditMemberRole(member: ProjectMember): boolean {
  if (!canAdmin.value) return false
  if (member.user_id === user.value?.id) return false
  if (member.role === 'owner' && !isOwner.value) return false
  return true
}

async function onMemberRoleChange(member: ProjectMember, role: string) {
  if (!projectId.value || !canEditMemberRole(member) || roleUpdatingId.value) return
  if (role === member.role) return
  roleUpdatingId.value = member.user_id
  error.value = null
  notice.value = null
  try {
    const updated = await updateMember(projectId.value, member.user_id, role)
    members.value = members.value.map((row) =>
      row.user_id === updated.user_id ? updated : row,
    )
    notice.value = t('projects.roleUpdated', { email: updated.email, role: updated.role })
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('projects.errors.roleUpdate')
    await load()
  } finally {
    roleUpdatingId.value = null
  }
}

async function onImportSaved() {
  importOpen.value = false
  await loadWorkspaces()
  if (project.value) {
    project.value = await getProject(projectId.value)
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="mx-auto w-full max-w-5xl animate-fade-up space-y-8 pb-16">
    <AppSplash v-if="loading && !project" compact :message="t('projects.loading')" />
    <p v-else-if="error && !project" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>

    <template v-if="project">
      <header class="space-y-5">
        <p class="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--lp-muted)]">
          {{ t('projects.eyebrow') }}
        </p>

        <div v-if="editingName && canAdmin" class="flex flex-wrap items-end gap-3">
          <label class="min-w-[16rem] flex-1 space-y-2">
            <span class="lp-label">{{ t('projects.name') }}</span>
            <input v-model="nameDraft" type="text" class="lp-input" required minlength="2">
          </label>
          <button type="button" class="lp-btn-primary" :disabled="renaming" @click="onRename">
            {{ t('projects.saveName') }}
          </button>
          <button
            type="button"
            class="lp-btn-ghost"
            @click="editingName = false; nameDraft = project.name"
          >
            {{ t('common.cancel') }}
          </button>
        </div>
        <div v-else class="flex flex-wrap items-center gap-3">
          <h1 class="text-4xl font-semibold tracking-tight text-[var(--lp-text)]">
            {{ project.name }}
          </h1>
          <button
            v-if="canAdmin"
            type="button"
            class="rounded-md border border-[var(--lp-line)] bg-transparent px-2.5 py-1 text-xs font-medium text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/50 hover:text-[var(--lp-accent)]"
            @click="editingName = true; nameDraft = project.name"
          >
            {{ t('projects.rename') }}
          </button>
        </div>

        <p class="text-sm text-[var(--lp-muted)]">
          <span class="font-mono">{{ project.slug }}</span>
          <span class="mx-1.5 text-[var(--lp-line)]">•</span>
          {{ t('projects.workspaceCount', { count: project.workspace_count }) }}
        </p>

        <div class="flex flex-wrap gap-2.5">
          <NuxtLink :to="workspacesHref" class="lp-btn-primary">
            <span class="material-symbols-outlined text-base">folder_open</span>
            {{ t('projects.viewWorkspaces') }}
          </NuxtLink>
          <NuxtLink :to="provisionHref" class="lp-btn-ghost">
            <span class="material-symbols-outlined text-base">add</span>
            {{ t('projects.createWorkspace') }}
          </NuxtLink>
          <button type="button" class="lp-btn-ghost" @click="importOpen = true">
            <span class="material-symbols-outlined text-base">download</span>
            {{ t('projects.importRepo') }}
          </button>
        </div>
      </header>

      <p
        v-if="notice"
        class="rounded-lg border border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 px-4 py-3 text-sm text-[var(--lp-ok)]"
      >
        {{ notice }}
      </p>
      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>

      <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-5 sm:p-6">
        <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold text-[var(--lp-text)]">
              {{ t('projects.workspacesTitle') }}
            </h2>
            <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ t('projects.workspacesBlurb') }}</p>
          </div>
          <NuxtLink
            :to="workspacesHref"
            class="rounded-md border border-[var(--lp-line)] px-3 py-1.5 text-xs font-medium text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/40"
          >
            {{ t('projects.viewWorkspaces') }}
          </NuxtLink>
        </div>

        <AppSplash
          v-if="workspacesLoading"
          compact
          :message="t('common.loading')"
        />
        <div
          v-else-if="workspaces.length === 0"
          class="rounded-xl border border-dashed border-[var(--lp-line)] px-4 py-10 text-center"
        >
          <p class="text-sm text-[var(--lp-muted)]">{{ t('projects.emptyWorkspaces') }}</p>
          <div class="mt-4 flex flex-wrap justify-center gap-2">
            <button type="button" class="lp-btn-ghost text-xs" @click="importOpen = true">
              {{ t('projects.importRepo') }}
            </button>
            <NuxtLink :to="provisionHref" class="lp-btn-primary text-xs">
              {{ t('projects.createWorkspace') }}
            </NuxtLink>
          </div>
        </div>
        <ul v-else class="divide-y divide-[var(--lp-line)]">
          <li
            v-for="ws in workspaces"
            :key="ws.id"
            class="flex min-w-0 flex-wrap items-center justify-between gap-3 py-3.5 first:pt-1 last:pb-1"
          >
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-[var(--lp-text)]">{{ ws.name }}</p>
              <p class="mt-0.5 truncate font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--lp-muted)]">
                {{ workspaceMetaTokens(ws).join(' • ') }}
              </p>
            </div>
            <NuxtLink
              :to="`/workspaces/${ws.id}`"
              class="shrink-0 rounded-md border border-[var(--lp-line)] px-3 py-1.5 text-xs font-medium text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/40"
            >
              {{ t('projects.openWorkspace') }}
            </NuxtLink>
          </li>
        </ul>
      </section>

      <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-5 sm:p-6">
        <h2 class="mb-4 text-base font-semibold text-[var(--lp-text)]">{{ t('projects.members') }}</h2>
        <ul class="divide-y divide-[var(--lp-line)]">
          <li
            v-for="member in members"
            :key="member.user_id"
            class="flex min-w-0 flex-wrap items-center justify-between gap-3 py-3.5 first:pt-1 last:pb-1"
          >
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-[var(--lp-text)]">{{ member.display_name }}</p>
              <p class="truncate text-sm text-[var(--lp-muted)]">{{ member.email }}</p>
            </div>
            <select
              v-if="canEditMemberRole(member)"
              class="lp-input w-auto min-w-[8rem] shrink-0 py-1.5 text-xs uppercase"
              :value="member.role"
              :disabled="roleUpdatingId === member.user_id"
              @change="onMemberRoleChange(member, ($event.target as HTMLSelectElement).value)"
            >
              <option value="viewer">viewer</option>
              <option value="member">member</option>
              <option value="admin">admin</option>
              <option v-if="isOwner" value="owner">owner</option>
            </select>
            <span
              v-else
              class="inline-flex shrink-0 rounded-md bg-[var(--lp-accent)]/12 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--lp-accent)]"
            >
              {{ member.role }}
            </span>
          </li>
        </ul>
      </section>

      <section
        v-if="canAdmin"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-5 sm:p-6"
      >
        <h2 class="text-base font-semibold text-[var(--lp-text)]">{{ t('projects.invites') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ t('projects.inviteOrgMembersBlurb') }}</p>
        <form class="lp-form-row mt-4" @submit.prevent="onInvite">
          <label class="block space-y-2">
            <span class="lp-label">{{ t('auth.email') }}</span>
            <select v-model="inviteForm.email" class="lp-input" required>
              <option disabled value="">{{ t('projects.selectOrgMember') }}</option>
              <option
                v-for="candidate in inviteCandidates"
                :key="candidate.user_id"
                :value="candidate.email"
              >
                {{ candidate.display_name }} ({{ candidate.email }})
              </option>
            </select>
          </label>
          <label class="block space-y-2">
            <span class="lp-label">{{ t('org.role') }}</span>
            <select v-model="inviteForm.role" class="lp-input">
              <option value="viewer">viewer</option>
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <button
            type="submit"
            class="lp-btn-primary"
            :disabled="inviting || !inviteForm.email || inviteCandidates.length === 0"
          >
            {{ t('projects.sendInvite') }}
          </button>
        </form>
        <p v-if="inviteCandidates.length === 0" class="mt-3 text-xs text-[var(--lp-muted)]">
          {{ t('projects.noInviteCandidates') }}
        </p>
        <ul v-if="invites.length" class="mt-4 divide-y divide-[var(--lp-line)]">
          <li
            v-for="invite in invites"
            :key="invite.id"
            class="flex min-w-0 items-center justify-between gap-3 py-3"
          >
            <div class="min-w-0">
              <p class="truncate text-sm">{{ invite.email }}</p>
              <p class="font-mono text-xs uppercase text-[var(--lp-muted)]">{{ invite.role }}</p>
            </div>
            <button
              v-if="!invite.accepted_at"
              type="button"
              class="lp-btn-ghost text-xs"
              @click="onRevoke(invite.id)"
            >
              {{ t('org.revoke') }}
            </button>
          </li>
        </ul>
      </section>
    </template>

    <RepoImporterModal
      v-model:open="importOpen"
      :launchpad-project-id="projectId"
      @saved="onImportSaved"
    />
  </div>
</template>
