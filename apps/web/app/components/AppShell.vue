<script setup lang="ts">
import type { Environment } from '~/types/environment'
import type { CatalogService } from '~/types/catalog'
import type { WorkspaceListItem } from '~/types/provisioning'

const route = useRoute()
const { t } = useI18n()
const { user, logout } = useAuth()
const { orgs, activeOrgId, setActiveOrg } = useOrgs()
const { environments, refresh: refreshEnvironments } = useEnvironments()
const { listWorkspaces } = useProvisioning()
const { listServices } = useCatalog()

const terminalOpen = useState('lp-terminal-open', () => false)
const activeTerminalWsPath = useState<string | null>('lp-terminal-ws-path', () => null)
const mobileNavOpen = ref(false)

const navItems = computed(() => [
  { key: 'home', label: t('nav.home'), to: '/home', icon: 'home', match: (path: string) => path === '/home' },
  { key: 'environments', label: t('nav.environments'), to: '/environments', icon: 'dashboard', match: (path: string) => path.startsWith('/environments') },
  { key: 'launch', label: t('nav.launch'), to: '/launch', icon: 'rocket_launch', match: (path: string) => path.startsWith('/launch') },
  { key: 'catalog', label: t('nav.catalog'), to: '/catalog', icon: 'inventory_2', match: (path: string) => path.startsWith('/catalog') },
  { key: 'workspaces', label: t('nav.workspaces'), to: '/workspaces', icon: 'layers', match: (path: string) => path.startsWith('/workspaces') },
  { key: 'provision', label: t('nav.provision'), to: '/provision', icon: 'schema', match: (path: string) => path.startsWith('/provision') },
  { key: 'integrations', label: t('nav.integrations'), to: '/integrations', icon: 'hub', match: (path: string) => path.startsWith('/integrations') },
  { key: 'organization', label: t('nav.organization'), to: '/org', icon: 'group', match: (path: string) => path.startsWith('/org') },
  { key: 'settings', label: t('nav.settings'), to: '/settings', icon: 'settings', match: (path: string) => path.startsWith('/settings') },
  { key: 'docs', label: t('nav.docs'), to: '/docs', icon: 'menu_book', match: (path: string) => path.startsWith('/docs') },
])

type NavSearchHit = {
  id: string
  label: string
  subtitle: string
  to: string
  icon: string
  group: 'Pages' | 'Environments' | 'Workspaces' | 'Services'
}

const searchQuery = ref('')
const searchOpen = ref(false)
const searchFocused = ref(false)
const activeHitIndex = ref(0)
const searchInputRef = ref<HTMLInputElement | null>(null)
const workspaces = ref<WorkspaceListItem[]>([])
const catalogServices = ref<CatalogService[]>([])
const searchIndexReady = ref(false)

const filteredNavItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return navItems.value
  return navItems.value.filter((item) => item.label.toLowerCase().includes(q))
})

const searchHits = computed((): NavSearchHit[] => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return []

  const hits: NavSearchHit[] = []

  for (const item of navItems.value) {
    if (item.label.toLowerCase().includes(q) || item.to.toLowerCase().includes(q)) {
      hits.push({
        id: `nav-${item.to}`,
        label: item.label,
        subtitle: item.to,
        to: item.to,
        icon: item.icon,
        group: 'Pages',
      })
    }
  }

  for (const env of environments.value as Environment[]) {
    const hay = `${env.name} ${env.git_repo_url} ${env.git_branch} ${env.status}`.toLowerCase()
    if (!hay.includes(q)) continue
    hits.push({
      id: `env-${env.id}`,
      label: env.name,
      subtitle: `${env.status} · ${env.git_branch}`,
      to: `/environments/${env.id}`,
      icon: 'deployed_code',
      group: 'Environments',
    })
  }

  for (const ws of workspaces.value) {
    const hay = `${ws.name} ${ws.provider} ${ws.engine} ${ws.status}`.toLowerCase()
    if (!hay.includes(q)) continue
    hits.push({
      id: `ws-${ws.id}`,
      label: ws.name,
      subtitle: `${ws.provider} · ${ws.engine}`,
      to: `/workspaces/${ws.id}`,
      icon: 'layers',
      group: 'Workspaces',
    })
  }

  for (const svc of catalogServices.value) {
    const hay = `${svc.name} ${svc.owner} ${svc.tier} ${svc.template_id}`.toLowerCase()
    if (!hay.includes(q)) continue
    hits.push({
      id: `svc-${svc.id}`,
      label: svc.name,
      subtitle: `${svc.tier} · ${svc.owner}`,
      to: `/catalog/${svc.id}`,
      icon: 'inventory_2',
      group: 'Services',
    })
  }

  return hits.slice(0, 24)
})

const showSearchPanel = computed(
  () => searchFocused.value && (searchQuery.value.trim().length > 0 || searchOpen.value),
)

function isActive(match: (path: string) => boolean): boolean {
  return match(route.path)
}

function onLogout() {
  logout()
  void navigateTo('/login')
}

function onOrgChange(orgId: string) {
  setActiveOrg(orgId || null)
  if (import.meta.client) {
    window.location.reload()
  }
}

function openCreateEnv() {
  void navigateTo('/launch')
  mobileNavOpen.value = false
}

function toggleTerminal() {
  terminalOpen.value = !terminalOpen.value
  mobileNavOpen.value = false
}

async function ensureSearchIndex() {
  if (searchIndexReady.value) return
  searchIndexReady.value = true
  try {
    await Promise.all([
      refreshEnvironments().catch(() => undefined),
      listWorkspaces()
        .then((rows) => {
          workspaces.value = rows
        })
        .catch(() => undefined),
      listServices()
        .then((rows) => {
          catalogServices.value = rows
        })
        .catch(() => undefined),
    ])
  } catch {
    // Search still works for nav pages without remote indexes.
  }
}

function onSearchFocus() {
  searchFocused.value = true
  searchOpen.value = true
  void ensureSearchIndex()
}

function onSearchBlur() {
  // Delay so click on a result registers before panel closes.
  window.setTimeout(() => {
    searchFocused.value = false
    searchOpen.value = false
  }, 150)
}

function clearSearch() {
  searchQuery.value = ''
  activeHitIndex.value = 0
}

async function goToHit(hit: NavSearchHit) {
  clearSearch()
  searchOpen.value = false
  mobileNavOpen.value = false
  await navigateTo(hit.to)
}

function onSearchKeydown(event: KeyboardEvent) {
  const hits = searchHits.value
  if (event.key === 'Escape') {
    clearSearch()
    searchInputRef.value?.blur()
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (!hits.length) return
    activeHitIndex.value = (activeHitIndex.value + 1) % hits.length
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (!hits.length) return
    activeHitIndex.value = (activeHitIndex.value - 1 + hits.length) % hits.length
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const hit = hits[activeHitIndex.value] ?? hits[0]
    if (hit) void goToHit(hit)
  }
}

watch(searchQuery, () => {
  activeHitIndex.value = 0
})

watch(
  () => route.fullPath,
  () => {
    mobileNavOpen.value = false
    clearSearch()
  },
)
</script>

<template>
  <div class="relative min-h-screen">
    <!-- Mobile nav backdrop -->
    <button
      v-if="mobileNavOpen"
      type="button"
      class="fixed inset-0 z-40 bg-[var(--lp-ink)]/70 backdrop-blur-sm lg:hidden"
      :aria-label="t('shell.closeNav')"
      @click="mobileNavOpen = false"
    />

    <!-- Sidebar -->
    <aside
      class="fixed left-0 top-0 z-50 flex h-full w-[var(--lp-sidebar-w)] flex-col border-r border-[var(--lp-line)] bg-[var(--lp-panel)]/90 py-6 backdrop-blur-md transition-transform duration-300 lg:translate-x-0"
      :class="mobileNavOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="px-6 mb-6">
        <NuxtLink to="/" class="block">
          <BrandLogo size="sm" />
        </NuxtLink>
        <div class="mt-5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80 p-3">
          <p class="lp-label mb-1">{{ t('shell.signedIn') }}</p>
          <p class="truncate text-sm font-medium text-[var(--lp-text)]">
            {{ user?.email ?? '-' }}
          </p>
          <label v-if="orgs.length" class="mt-3 block space-y-1">
            <span class="lp-label">{{ t('shell.organization') }}</span>
            <select
              class="lp-input py-1.5 text-xs"
              :value="activeOrgId ?? ''"
              @change="onOrgChange(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="org in orgs" :key="org.id" :value="org.id">
                {{ org.name }} ({{ org.role }})
              </option>
            </select>
          </label>
        </div>
      </div>

      <nav class="flex-1 space-y-1 overflow-y-auto px-3">
        <div class="relative mb-3 sm:hidden">
          <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-sm text-[var(--lp-muted)]">
            search
          </span>
          <input
            v-model="searchQuery"
            type="search"
            class="lp-input pl-10"
            :placeholder="t('shell.filterMenu')"
            :aria-label="t('shell.filterNav')"
            @focus="onSearchFocus"
          >
        </div>
        <p
          v-if="searchQuery.trim() && !filteredNavItems.length"
          class="px-3 py-2 text-xs text-[var(--lp-muted)]"
        >
          {{ t('shell.noMenuMatches') }}
        </p>
        <NuxtLink
          v-for="item in filteredNavItems"
          :key="item.to"
          :to="item.to"
          class="lp-nav-link"
          :class="{ 'lp-nav-link-active': isActive(item.match) }"
        >
          <span
            class="material-symbols-outlined text-[1.1rem]"
            :class="{ filled: isActive(item.match) }"
          >
            {{ item.icon }}
          </span>
          <span>{{ item.label }}</span>
        </NuxtLink>

        <a
          href="http://localhost:8000/docs"
          target="_blank"
          rel="noreferrer"
          class="lp-nav-link"
        >
          <span class="material-symbols-outlined text-[1.1rem]">description</span>
          <span>{{ t('shell.apiDocs') }}</span>
        </a>
      </nav>

      <div class="mt-auto space-y-1 border-t border-[var(--lp-line)] px-3 pt-4">
        <button type="button" class="lp-btn-primary mb-3 w-full text-xs uppercase tracking-wide" @click="openCreateEnv">
          <span class="material-symbols-outlined text-base">add</span>
          {{ t('shell.newEnvironment') }}
        </button>
        <button type="button" class="lp-nav-link w-full text-left" @click="toggleTerminal">
          <span class="material-symbols-outlined text-[1.1rem]">terminal</span>
          <span>{{ t('shell.terminal') }}</span>
        </button>
        <button
          v-if="user"
          type="button"
          class="lp-nav-link w-full text-left"
          @click="onLogout"
        >
          <span class="material-symbols-outlined text-[1.1rem]">logout</span>
          <span>{{ t('shell.logout') }}</span>
        </button>
      </div>
    </aside>

    <!-- Main column -->
    <div class="flex min-h-screen flex-col lg:ml-[var(--lp-sidebar-w)]">
      <header
        class="sticky top-0 z-30 flex h-[var(--lp-header-h)] shrink-0 items-center justify-between gap-4 border-b border-[var(--lp-line)] bg-[var(--lp-ink)]/80 px-4 backdrop-blur-md sm:px-8"
      >
        <div class="flex flex-1 items-center gap-3">
          <button
            type="button"
            class="rounded-lg p-2 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)] lg:hidden"
            :aria-label="t('shell.openNav')"
            @click="mobileNavOpen = true"
          >
            <span class="material-symbols-outlined">menu</span>
          </button>
          <div class="relative hidden w-full max-w-xl sm:block">
            <span class="material-symbols-outlined absolute left-3 top-1/2 z-10 -translate-y-1/2 text-sm text-[var(--lp-muted)]">
              search
            </span>
            <input
              ref="searchInputRef"
              v-model="searchQuery"
              type="search"
              class="lp-input pl-10"
              :placeholder="t('shell.searchPlaceholder')"
              :aria-label="t('shell.searchAria')"
              aria-autocomplete="list"
              :aria-expanded="showSearchPanel"
              @focus="onSearchFocus"
              @blur="onSearchBlur"
              @keydown="onSearchKeydown"
            >
            <div
              v-if="showSearchPanel && searchQuery.trim()"
              class="absolute left-0 right-0 top-[calc(100%+0.5rem)] z-40 max-h-80 overflow-y-auto rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl"
              role="listbox"
            >
              <p
                v-if="!searchHits.length"
                class="px-4 py-3 text-sm text-[var(--lp-muted)]"
              >
                {{ t('shell.noMatches', { query: searchQuery.trim() }) }}
              </p>
              <template v-else>
                <button
                  v-for="(hit, index) in searchHits"
                  :key="hit.id"
                  type="button"
                  role="option"
                  class="flex w-full items-start gap-3 px-4 py-2.5 text-left transition hover:bg-[var(--lp-panel-2)]"
                  :class="index === activeHitIndex ? 'bg-[var(--lp-panel-2)]' : ''"
                  :aria-selected="index === activeHitIndex"
                  @mousedown.prevent="goToHit(hit)"
                >
                  <span class="material-symbols-outlined mt-0.5 text-[1.1rem] text-[var(--lp-accent)]">
                    {{ hit.icon }}
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-sm font-medium text-[var(--lp-text)]">{{ hit.label }}</span>
                    <span class="mt-0.5 block truncate font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                      {{ t(`shell.groups.${hit.group}`) }} · {{ hit.subtitle }}
                    </span>
                  </span>
                </button>
              </template>
            </div>
          </div>
        </div>

        <div class="flex items-center gap-2 sm:gap-3">
          <PreferenceControls compact />
          <button type="button" class="lp-btn-primary hidden text-xs uppercase tracking-wide sm:inline-flex" @click="openCreateEnv">
            {{ t('shell.launchPreview') }}
          </button>
          <NuxtLink
            to="/provision"
            class="lp-btn-ghost hidden text-xs uppercase tracking-wide md:inline-flex"
          >
            <span class="material-symbols-outlined text-base">tune</span>
            {{ t('common.advanced') }}
          </NuxtLink>
          <NotificationBell />
          <div
            class="ml-1 flex h-8 w-8 items-center justify-center rounded-full border border-[var(--lp-line)] bg-[var(--lp-panel-2)] font-mono text-xs text-[var(--lp-accent)]"
            :title="user?.email ?? ''"
          >
            {{ (user?.email?.[0] ?? 'U').toUpperCase() }}
          </div>
        </div>
      </header>

      <main class="flex-1 px-4 py-8 sm:px-8">
        <div class="mx-auto w-full max-w-[1440px]">
          <slot />
        </div>
      </main>
    </div>

    <!-- Floating terminal toggle -->
    <button
      type="button"
      class="fixed bottom-8 right-8 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--lp-accent)] text-[var(--lp-ink)] shadow-lg shadow-[var(--lp-accent)]/25 transition hover:scale-105 active:scale-95"
      :aria-label="t('shell.toggleTerminal')"
      @click="toggleTerminal"
    >
      <span class="material-symbols-outlined filled text-2xl">terminal</span>
    </button>

    <!-- Terminal drawer -->
    <Teleport to="body">
      <div
        v-if="terminalOpen"
        class="fixed inset-0 z-[60] bg-[var(--lp-ink)]/50 backdrop-blur-sm transition-opacity"
        @click.self="terminalOpen = false"
      />
      <aside
        class="fixed inset-y-0 right-0 z-[70] flex w-full max-w-[640px] flex-col border-l border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl transition-transform duration-300"
        :class="terminalOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none'"
        :aria-hidden="!terminalOpen"
      >
        <div class="flex h-16 items-center justify-between border-b border-[var(--lp-line)] px-5">
          <div>
            <h3 class="text-lg font-semibold text-[var(--lp-accent)]">{{ t('shell.terminalSandbox') }}</h3>
            <p class="font-mono text-[10px] text-[var(--lp-muted)]">
              {{
                activeTerminalWsPath
                  ? t('shell.liveSessionAttached')
                  : t('shell.openProvisionToAttach')
              }}
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-2 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            :aria-label="t('shell.closeTerminal')"
            @click="terminalOpen = false"
          >
            <span class="material-symbols-outlined">close</span>
          </button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <ClientOnly>
            <TerminalPanel
              v-if="activeTerminalWsPath && !route.path.startsWith('/provision') && !route.path.startsWith('/workspaces/')"
              :ws-path="activeTerminalWsPath"
            />
            <div
              v-else-if="activeTerminalWsPath && (route.path.startsWith('/provision') || route.path.startsWith('/workspaces/'))"
              class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]"
            >
              <p>{{ t('shell.liveSessionOnPage') }}</p>
              <button type="button" class="lp-btn-ghost" @click="terminalOpen = false">
                {{ t('shell.closeDrawer') }}
              </button>
            </div>
            <div v-else class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]">
              <p>{{ t('shell.noSandboxSession') }}</p>
              <NuxtLink
                to="/provision"
                class="lp-btn-primary inline-flex"
                @click="terminalOpen = false"
              >
                {{ t('shell.goToProvision') }}
              </NuxtLink>
            </div>
          </ClientOnly>
        </div>
      </aside>
    </Teleport>
  </div>
</template>
