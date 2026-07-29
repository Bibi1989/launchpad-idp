<script setup lang="ts">
const route = useRoute()
const { user, logout } = useAuth()
const { orgs, activeOrgId, setActiveOrg } = useOrgs()

const terminalOpen = useState('lp-terminal-open', () => false)
const createEnvOpen = useState('lp-create-env-open', () => false)
const activeTerminalWsPath = useState<string | null>('lp-terminal-ws-path', () => null)
const mobileNavOpen = ref(false)

const navItems = [
  { label: 'Environments', to: '/', icon: 'dashboard', match: (path: string) => path === '/' || path.startsWith('/environments') },
  { label: 'Launch', to: '/launch', icon: 'rocket_launch', match: (path: string) => path.startsWith('/launch') },
  { label: 'Workspaces', to: '/workspaces', icon: 'layers', match: (path: string) => path.startsWith('/workspaces') },
  { label: 'Dockerfiles', to: '/dockerfiles', icon: 'deployed_code', match: (path: string) => path.startsWith('/dockerfiles') },
  { label: 'Organization', to: '/org', icon: 'group', match: (path: string) => path.startsWith('/org') },
  { label: 'Provision', to: '/provision', icon: 'schema', match: (path: string) => path.startsWith('/provision') },
  { label: 'GitHub', to: '/integrations/github', icon: 'hub', match: (path: string) => path.startsWith('/integrations') },
  { label: 'Docs', to: '/docs', icon: 'menu_book', match: (path: string) => path.startsWith('/docs') },
] as const

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

watch(
  () => route.fullPath,
  () => {
    mobileNavOpen.value = false
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
      aria-label="Close navigation"
      @click="mobileNavOpen = false"
    />

    <!-- Sidebar -->
    <aside
      class="fixed left-0 top-0 z-50 flex h-full w-[var(--lp-sidebar-w)] flex-col border-r border-[var(--lp-line)] bg-[var(--lp-panel)]/90 py-6 backdrop-blur-md transition-transform duration-300 lg:translate-x-0"
      :class="mobileNavOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="px-6 mb-6">
        <NuxtLink to="/" class="block">
          <h2 class="text-xl font-semibold tracking-tight text-[var(--lp-accent)]">
            Launchpad
          </h2>
          <p class="mt-0.5 font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--lp-muted)]">
            IDP
          </p>
        </NuxtLink>
        <div class="mt-5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80 p-3">
          <p class="lp-label mb-1">Signed in</p>
          <p class="truncate text-sm font-medium text-[var(--lp-text)]">
            {{ user?.email ?? '—' }}
          </p>
          <label v-if="orgs.length" class="mt-3 block space-y-1">
            <span class="lp-label">Organization</span>
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

      <nav class="flex-1 space-y-1 px-3">
        <NuxtLink
          v-for="item in navItems"
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
          <span>API Docs</span>
        </a>
      </nav>

      <div class="mt-auto space-y-1 border-t border-[var(--lp-line)] px-3 pt-4">
        <button type="button" class="lp-btn-primary mb-3 w-full text-xs uppercase tracking-wide" @click="openCreateEnv">
          <span class="material-symbols-outlined text-base">add</span>
          New Environment
        </button>
        <button type="button" class="lp-nav-link w-full text-left" @click="toggleTerminal">
          <span class="material-symbols-outlined text-[1.1rem]">terminal</span>
          <span>Terminal</span>
        </button>
        <button
          v-if="user"
          type="button"
          class="lp-nav-link w-full text-left"
          @click="onLogout"
        >
          <span class="material-symbols-outlined text-[1.1rem]">logout</span>
          <span>Logout</span>
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
            aria-label="Open navigation"
            @click="mobileNavOpen = true"
          >
            <span class="material-symbols-outlined">menu</span>
          </button>
          <div class="relative hidden w-full max-w-xl sm:block">
            <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-sm text-[var(--lp-muted)]">
              search
            </span>
            <input
              type="search"
              class="lp-input pl-10"
              placeholder="Search environments, workspaces…"
              readonly
              tabindex="-1"
              aria-label="Search"
            >
          </div>
        </div>

        <div class="flex items-center gap-2 sm:gap-3">
          <button type="button" class="lp-btn-primary hidden text-xs uppercase tracking-wide sm:inline-flex" @click="openCreateEnv">
            Launch preview
          </button>
          <NuxtLink
            to="/provision"
            class="lp-btn-ghost hidden text-xs uppercase tracking-wide md:inline-flex"
          >
            <span class="material-symbols-outlined text-base">tune</span>
            Advanced
          </NuxtLink>
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
      aria-label="Toggle terminal"
      @click="toggleTerminal"
    >
      <span class="material-symbols-outlined filled text-2xl">terminal</span>
    </button>

    <!-- Terminal drawer -->
    <Teleport to="body">
      <div
        v-if="terminalOpen"
        class="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm transition-opacity"
        @click.self="terminalOpen = false"
      />
      <aside
        class="fixed inset-y-0 right-0 z-[70] flex w-full max-w-[640px] flex-col border-l border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl transition-transform duration-300"
        :class="terminalOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none'"
        :aria-hidden="!terminalOpen"
      >
        <div class="flex h-16 items-center justify-between border-b border-[var(--lp-line)] px-5">
          <div>
            <h3 class="text-lg font-semibold text-[var(--lp-accent)]">Terminal Sandbox</h3>
            <p class="font-mono text-[10px] text-[var(--lp-muted)]">
              {{
                activeTerminalWsPath
                  ? 'Live session attached'
                  : 'Open Provision → Generate to attach a live session'
              }}
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-2 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            aria-label="Close terminal"
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
              <p>Live session is attached on this page’s terminal panel.</p>
              <button type="button" class="lp-btn-ghost" @click="terminalOpen = false">
                Close drawer
              </button>
            </div>
            <div v-else class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]">
              <p>No active sandbox session.</p>
              <NuxtLink
                to="/provision"
                class="lp-btn-primary inline-flex"
                @click="terminalOpen = false"
              >
                Go to Provision
              </NuxtLink>
            </div>
          </ClientOnly>
        </div>
      </aside>
    </Teleport>

    <!-- Create environment modal -->
    <Teleport to="body">
      <div
        v-if="createEnvOpen"
        class="fixed inset-0 z-[80] flex items-center justify-center bg-[var(--lp-ink)]/80 p-4 backdrop-blur-md"
        @click.self="createEnvOpen = false"
      >
        <div class="lp-glass flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl shadow-2xl">
          <div class="flex items-start justify-between border-b border-[var(--lp-line)] px-6 py-5">
            <div>
              <h2 class="text-xl font-semibold text-[var(--lp-accent)]">Launch Environment</h2>
              <p class="mt-1 text-sm text-[var(--lp-muted)]">
                Provision an isolated namespace with TTL governance.
              </p>
            </div>
            <button
              type="button"
              class="rounded-lg p-2 text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
              aria-label="Close"
              @click="createEnvOpen = false"
            >
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <div class="overflow-y-auto p-6">
            <EnvironmentCreateForm
              :initial-workspace-id="typeof route.query.workspace === 'string' ? route.query.workspace : null"
              @created="(id) => { createEnvOpen = false; navigateTo(`/environments/${id}`) }"
            />
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
