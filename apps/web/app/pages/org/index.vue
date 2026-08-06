<script setup lang="ts">
import type { OrgInvite, OrgMember, OrgSsoMapping } from '~/types/auth'

const { t } = useI18n()
const {
  orgs,
  activeOrgId,
  listMembers,
  listInvites,
  createInvite,
  revokeInvite,
  listSsoMappings,
  upsertSsoMapping,
  deleteSsoMapping,
} = useOrgs()

const orgId = computed(() => activeOrgId.value || orgs.value[0]?.id || null)
const activeOrg = computed(() => orgs.value.find((org) => org.id === orgId.value) || null)
const canAdmin = computed(() => {
  const role = activeOrg.value?.role
  return role === 'owner' || role === 'admin'
})

const members = ref<OrgMember[]>([])
const invites = ref<OrgInvite[]>([])
const mappings = ref<OrgSsoMapping[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const inviteForm = reactive({ email: '', role: 'member' })
const mappingForm = reactive({ group_name: '', role: 'member' })
const inviting = ref(false)
const mappingBusy = ref(false)

async function load() {
  if (!orgId.value) return
  loading.value = true
  error.value = null
  try {
    const [memberRows, inviteRows, mappingRows] = await Promise.all([
      listMembers(orgId.value),
      listInvites(orgId.value),
      listSsoMappings(orgId.value),
    ])
    members.value = memberRows
    invites.value = inviteRows
    mappings.value = mappingRows
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.load')
  } finally {
    loading.value = false
  }
}

async function onInvite() {
  if (!orgId.value || inviting.value) return
  inviting.value = true
  notice.value = null
  error.value = null
  try {
    const created = await createInvite(orgId.value, {
      email: inviteForm.email,
      role: inviteForm.role,
    })
    inviteForm.email = ''
    await load()
    if (created.email_sent) {
      notice.value = t('org.inviteEmailed', { email: created.email })
    } else if (created.invite_url) {
      notice.value = t('org.inviteCreatedSmtp', { url: created.invite_url })
    } else {
      notice.value = t('org.inviteCreatedFor', { email: created.email })
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.invite')
  } finally {
    inviting.value = false
  }
}

async function onRevoke(inviteId: string) {
  if (!orgId.value) return
  try {
    await revokeInvite(orgId.value, inviteId)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.revoke')
  }
}

async function onAddMapping() {
  if (!orgId.value || mappingBusy.value) return
  mappingBusy.value = true
  error.value = null
  try {
    await upsertSsoMapping(orgId.value, {
      group_name: mappingForm.group_name,
      role: mappingForm.role,
    })
    mappingForm.group_name = ''
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.mapping')
  } finally {
    mappingBusy.value = false
  }
}

async function onDeleteMapping(mappingId: string) {
  if (!orgId.value) return
  try {
    await deleteSsoMapping(orgId.value, mappingId)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.deleteMapping')
  }
}

watch(orgId, () => {
  void load()
}, { immediate: true })
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="lp-label">{{ t('nav.organization') }}</p>
      <h1 class="text-3xl font-semibold tracking-tight">
        {{ activeOrg?.name || t('org.settings') }}
      </h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('org.blurbDetail') }}
      </p>
    </header>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <p v-if="notice" class="rounded-lg border border-[var(--lp-ok)]/30 bg-[var(--lp-ok)]/10 px-4 py-3 text-sm text-[var(--lp-ok)]">
      {{ notice }}
    </p>
    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('common.loading') }}</p>

    <section class="lp-glass space-y-4 rounded-xl p-5">
      <div class="flex items-center justify-between gap-3">
        <h2 class="text-lg font-semibold">{{ t('org.members') }}</h2>
        <span class="font-mono text-xs text-[var(--lp-muted)]">{{ members.length }}</span>
      </div>
      <ul class="divide-y divide-[var(--lp-line)]">
        <li
          v-for="member in members"
          :key="member.user_id"
          class="flex flex-wrap items-center justify-between gap-2 py-3"
        >
          <div>
            <p class="text-sm font-medium">{{ member.display_name }}</p>
            <p class="font-mono text-xs text-[var(--lp-muted)]">{{ member.email }}</p>
          </div>
          <span class="rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-xs uppercase">
            {{ member.role }}
          </span>
        </li>
      </ul>
    </section>

    <section v-if="canAdmin" class="lp-glass space-y-4 rounded-xl p-5">
      <h2 class="text-lg font-semibold">{{ t('org.inviteEmail') }}</h2>
      <form class="flex flex-wrap items-end gap-3" @submit.prevent="onInvite">
        <label class="block min-w-[16rem] flex-1 space-y-1">
          <span class="lp-label">{{ t('common.email') }}</span>
          <input
            v-model="inviteForm.email"
            type="email"
            required
            class="lp-input w-full"
            placeholder="teammate@company.com"
          >
        </label>
        <label class="block space-y-1">
          <span class="lp-label">{{ t('common.role') }}</span>
          <select v-model="inviteForm.role" class="lp-input">
            <option value="viewer">viewer</option>
            <option value="member">member</option>
            <option value="admin">admin</option>
            <option value="owner">owner</option>
          </select>
        </label>
        <button type="submit" class="lp-btn-primary" :disabled="inviting">
          {{ inviting ? t('org.sending') : t('org.sendInvite') }}
        </button>
      </form>

      <ul v-if="invites.length" class="divide-y divide-[var(--lp-line)]">
        <li
          v-for="invite in invites"
          :key="invite.id"
          class="flex flex-wrap items-center justify-between gap-2 py-3"
        >
          <div>
            <p class="font-mono text-sm">{{ invite.email }}</p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ invite.role }} · {{ t('org.inviteExpires') }} {{ new Date(invite.expires_at).toLocaleString() }}
            </p>
          </div>
          <button type="button" class="lp-btn-ghost text-xs" @click="onRevoke(invite.id)">
            {{ t('org.revoke') }}
          </button>
        </li>
      </ul>
    </section>

    <section v-if="canAdmin" class="lp-glass space-y-4 rounded-xl p-5">
      <h2 class="text-lg font-semibold">{{ t('org.ssoTitle') }}</h2>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('org.ssoBlurbDetail') }}
      </p>
      <form class="flex flex-wrap items-end gap-3" @submit.prevent="onAddMapping">
        <label class="block min-w-[16rem] flex-1 space-y-1">
          <span class="lp-label">{{ t('org.groupName') }}</span>
          <input
            v-model="mappingForm.group_name"
            type="text"
            required
            class="lp-input w-full"
            placeholder="launchpad-admins"
          >
        </label>
        <label class="block space-y-1">
          <span class="lp-label">{{ t('common.role') }}</span>
          <select v-model="mappingForm.role" class="lp-input">
            <option value="viewer">viewer</option>
            <option value="member">member</option>
            <option value="admin">admin</option>
            <option value="owner">owner</option>
          </select>
        </label>
        <button type="submit" class="lp-btn-primary" :disabled="mappingBusy">
          {{ mappingBusy ? t('common.saving') : t('org.saveMapping') }}
        </button>
      </form>

      <ul v-if="mappings.length" class="divide-y divide-[var(--lp-line)]">
        <li
          v-for="mapping in mappings"
          :key="mapping.id"
          class="flex flex-wrap items-center justify-between gap-2 py-3"
        >
          <div>
            <p class="font-mono text-sm">{{ mapping.group_name }}</p>
            <p class="text-xs text-[var(--lp-muted)]">→ {{ mapping.role }}</p>
          </div>
          <button type="button" class="lp-btn-ghost text-xs" @click="onDeleteMapping(mapping.id)">
            {{ t('org.remove') }}
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>
