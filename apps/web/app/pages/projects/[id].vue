<script setup lang="ts">
import type { OrgMember, ProjectInvite, ProjectMember, ProjectSummary } from '~/types/auth'

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
const { activeOrgId, listMembers: listOrgMembers } = useOrgs()
const { user } = useAuth()

const projectId = computed(() => String(route.params.id || ''))
const project = ref<ProjectSummary | null>(null)
const members = ref<ProjectMember[]>([])
const invites = ref<ProjectInvite[]>([])
const orgMembers = ref<OrgMember[]>([])
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

async function load() {
  if (!projectId.value) return
  loading.value = true
  error.value = null
  try {
    project.value = await getProject(projectId.value)
    nameDraft.value = project.value.name
    members.value = await listMembers(projectId.value)
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

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="w-full animate-fade-up space-y-8 pb-12">
    <NuxtLink
      to="/projects"
      class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
    >
      <span class="material-symbols-outlined text-sm">arrow_back</span>
      {{ t('projects.back') }}
    </NuxtLink>

    <AppSplash v-if="loading && !project" compact :message="t('projects.loading')" />
    <p v-else-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>

    <template v-if="project">
      <header class="space-y-3">
        <p class="lp-label">{{ t('projects.eyebrow') }}</p>
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
          <h1 class="text-3xl font-semibold tracking-tight">{{ project.name }}</h1>
          <button
            v-if="canAdmin"
            type="button"
            class="lp-btn-ghost text-xs"
            @click="editingName = true; nameDraft = project.name"
          >
            {{ t('projects.rename') }}
          </button>
        </div>
        <p class="font-mono text-xs text-[var(--lp-muted)]">
          {{ project.slug }} · {{ t('projects.workspaceCount', { count: project.workspace_count }) }}
        </p>
      </header>

      <p v-if="notice" class="rounded-lg border border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 px-4 py-3 text-sm text-[var(--lp-ok)]">
        {{ notice }}
      </p>

      <section class="lp-glass space-y-4 rounded-xl p-5">
        <h2 class="text-sm font-semibold">{{ t('projects.members') }}</h2>
        <ul class="divide-y divide-[var(--lp-line)]">
          <li
            v-for="member in members"
            :key="member.user_id"
            class="flex min-w-0 flex-wrap items-center justify-between gap-3 py-3"
          >
            <div class="min-w-0">
              <p class="truncate text-sm font-medium">{{ member.display_name }}</p>
              <p class="truncate font-mono text-xs text-[var(--lp-muted)]">{{ member.email }}</p>
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
            <span v-else class="lp-label shrink-0">{{ member.role }}</span>
          </li>
        </ul>
      </section>

      <section v-if="canAdmin" class="lp-glass space-y-4 rounded-xl p-5">
        <h2 class="text-sm font-semibold">{{ t('projects.invites') }}</h2>
        <p class="text-xs text-[var(--lp-muted)]">{{ t('projects.inviteOrgMembersBlurb') }}</p>
        <form class="lp-form-row" @submit.prevent="onInvite">
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
        <p v-if="inviteCandidates.length === 0" class="text-xs text-[var(--lp-muted)]">
          {{ t('projects.noInviteCandidates') }}
        </p>
        <ul class="divide-y divide-[var(--lp-line)]">
          <li
            v-for="invite in invites"
            :key="invite.id"
            class="flex min-w-0 items-center justify-between gap-3 py-3"
          >
            <div class="min-w-0">
              <p class="truncate text-sm">{{ invite.email }}</p>
              <p class="font-mono text-xs text-[var(--lp-muted)]">{{ invite.role }}</p>
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
  </div>
</template>
