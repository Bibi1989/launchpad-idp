<script setup lang="ts">
import type { OrgInvite, OrgMember, OrgPlanSummary, OrgSsoMapping } from '~/types/auth'

const { t } = useI18n()
const route = useRoute()
const {
  orgs,
  activeOrgId,
  listMembers,
  updateMember,
  listInvites,
  createInvite,
  revokeInvite,
  listSsoMappings,
  upsertSsoMapping,
  deleteSsoMapping,
} = useOrgs()
const { user } = useAuth()
const { getPlan, startCheckout, openPortal } = useBilling()

const orgId = computed(() => activeOrgId.value || orgs.value[0]?.id || null)
const activeOrg = computed(() => orgs.value.find((org) => org.id === orgId.value) || null)
const canAdmin = computed(() => {
  const role = activeOrg.value?.role
  return role === 'owner' || role === 'admin'
})
const isOwner = computed(() => activeOrg.value?.role === 'owner')
const roleUpdatingId = ref<string | null>(null)

const members = ref<OrgMember[]>([])
const invites = ref<OrgInvite[]>([])
const mappings = ref<OrgSsoMapping[]>([])
const plan = ref<OrgPlanSummary | null>(null)
const loading = ref(false)
const billingBusy = ref(false)
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
    const [memberRows, inviteRows, mappingRows, planRow] = await Promise.all([
      listMembers(orgId.value),
      listInvites(orgId.value),
      listSsoMappings(orgId.value),
      getPlan(orgId.value).catch(() => null),
    ])
    members.value = memberRows
    invites.value = inviteRows
    mappings.value = mappingRows
    plan.value = planRow
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.load')
  } finally {
    loading.value = false
  }
}

async function onUpgrade() {
  if (!orgId.value || billingBusy.value) return
  billingBusy.value = true
  error.value = null
  try {
    const url = await startCheckout(orgId.value)
    if (import.meta.client) window.location.href = url
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.billing')
  } finally {
    billingBusy.value = false
  }
}

async function onManageBilling() {
  if (!orgId.value || billingBusy.value) return
  billingBusy.value = true
  error.value = null
  try {
    const url = await openPortal(orgId.value)
    if (import.meta.client) window.location.href = url
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.billing')
  } finally {
    billingBusy.value = false
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
    } else if (created.email_error) {
      notice.value = t('org.inviteEmailFailed', {
        email: created.email,
        error: created.email_error,
        url: created.invite_url || '-',
      })
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

function canEditMemberRole(member: OrgMember): boolean {
  if (!canAdmin.value) return false
  if (member.user_id === user.value?.id) return false
  if (member.role === 'owner' && !isOwner.value) return false
  return true
}

async function onMemberRoleChange(member: OrgMember, role: string) {
  if (!orgId.value || !canEditMemberRole(member) || roleUpdatingId.value) return
  if (role === member.role) return
  roleUpdatingId.value = member.user_id
  error.value = null
  notice.value = null
  try {
    const updated = await updateMember(orgId.value, member.user_id, role)
    members.value = members.value.map((row) =>
      row.user_id === updated.user_id ? updated : row,
    )
    notice.value = t('org.roleUpdated', { email: updated.email, role: updated.role })
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('org.errors.roleUpdate')
    await load()
  } finally {
    roleUpdatingId.value = null
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

onMounted(() => {
  const billing = route.query.billing
  if (billing === 'success') {
    notice.value = t('org.billingSuccess')
    void load()
  } else if (billing === 'cancel') {
    notice.value = t('org.billingCancel')
  }
})
</script>

<template>
  <div class="w-full space-y-6 animate-fade-up">
    <header class="space-y-2">
      <p class="lp-label">{{ t('nav.organization') }}</p>
      <h1 class="text-3xl font-semibold tracking-tight">
        {{ activeOrg?.name || t('org.settings') }}
      </h1>
      <p class="max-w-3xl text-sm leading-relaxed text-[var(--lp-muted)]">
        {{ t('org.blurbDetail') }}
      </p>
    </header>

    <section v-if="plan" class="lp-glass space-y-4 rounded-xl p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="space-y-1">
          <h2 class="text-lg font-semibold">{{ t('org.planTitle') }}</h2>
          <p class="text-sm text-[var(--lp-muted)]">
            {{ t('org.planBlurb', {
              plan: plan.plan,
              projects: `${plan.project_count}/${plan.max_projects}`,
              workspaces: `${plan.workspace_count}/${plan.max_workspaces}`,
              price: plan.pro_price_eur,
            }) }}
          </p>
        </div>
        <span class="rounded border border-[var(--lp-accent)]/40 px-2 py-0.5 font-mono text-xs uppercase text-[var(--lp-accent)]">
          {{ plan.plan }}
        </span>
      </div>
      <div v-if="canAdmin" class="flex flex-wrap gap-2">
        <button
          v-if="plan.plan !== 'pro'"
          type="button"
          class="lp-btn-primary"
          :disabled="billingBusy"
          @click="onUpgrade"
        >
          {{ t('org.upgradePro', { price: plan.pro_price_eur }) }}
        </button>
        <button
          v-else
          type="button"
          class="lp-btn-ghost"
          :disabled="billingBusy"
          @click="onManageBilling"
        >
          {{ t('org.manageBilling') }}
        </button>
      </div>
    </section>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <p
      v-if="notice"
      class="rounded-lg border border-[var(--lp-ok)]/30 bg-[var(--lp-ok)]/10 px-4 py-3 text-sm text-[var(--lp-ok)]"
    >
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
          class="flex flex-wrap items-center justify-between gap-3 py-3"
        >
          <div class="min-w-0 flex-1">
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
          <span
            v-else
            class="shrink-0 rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-xs uppercase"
          >
            {{ member.role }}
          </span>
        </li>
      </ul>
    </section>

    <section v-if="canAdmin" class="lp-glass space-y-4 rounded-xl p-5">
      <h2 class="text-lg font-semibold">{{ t('org.inviteEmail') }}</h2>
      <form class="lp-form-row" @submit.prevent="onInvite">
        <label class="block space-y-1.5">
          <span class="lp-label">{{ t('common.email') }}</span>
          <input
            v-model="inviteForm.email"
            type="email"
            required
            class="lp-input"
            placeholder="teammate@company.com"
          >
        </label>
        <label class="block space-y-1.5">
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
          class="flex flex-wrap items-center justify-between gap-3 py-3"
        >
          <div class="min-w-0 flex-1">
            <p class="truncate font-mono text-sm">{{ invite.email }}</p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ invite.role }} · {{ t('org.inviteExpires') }} {{ new Date(invite.expires_at).toLocaleString() }}
            </p>
          </div>
          <button type="button" class="lp-btn-ghost shrink-0 text-xs" @click="onRevoke(invite.id)">
            {{ t('org.revoke') }}
          </button>
        </li>
      </ul>
    </section>

    <section v-if="canAdmin" class="lp-glass space-y-4 rounded-xl p-5">
      <div class="space-y-1">
        <h2 class="text-lg font-semibold">{{ t('org.ssoTitle') }}</h2>
        <p class="max-w-3xl text-sm leading-relaxed text-[var(--lp-muted)]">
          {{ t('org.ssoBlurbDetail') }}
        </p>
      </div>
      <form class="lp-form-row" @submit.prevent="onAddMapping">
        <label class="block space-y-1.5">
          <span class="lp-label">{{ t('org.groupName') }}</span>
          <input
            v-model="mappingForm.group_name"
            type="text"
            required
            class="lp-input"
            placeholder="launchpad-admins"
          >
        </label>
        <label class="block space-y-1.5">
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
          class="flex flex-wrap items-center justify-between gap-3 py-3"
        >
          <div class="min-w-0 flex-1">
            <p class="truncate font-mono text-sm">{{ mapping.group_name }}</p>
            <p class="text-xs text-[var(--lp-muted)]">→ {{ mapping.role }}</p>
          </div>
          <button type="button" class="lp-btn-ghost shrink-0 text-xs" @click="onDeleteMapping(mapping.id)">
            {{ t('org.remove') }}
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>
