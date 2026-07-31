<script setup lang="ts">
import type { CatalogService, GoldenPathTemplate } from '~/types/catalog'

const route = useRoute()
const router = useRouter()
const { listTemplates, listServices } = useCatalog()

type CatalogTab = 'templates' | 'services'

const templates = ref<GoldenPathTemplate[]>([])
const services = ref<CatalogService[]>([])
const loading = ref(true)
const errorMessage = ref<string | null>(null)

const activeTab = computed<CatalogTab>(() => {
  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  return tab === 'services' ? 'services' : 'templates'
})

function setTab(tab: CatalogTab) {
  void router.replace({
    path: '/catalog',
    query: tab === 'templates' ? {} : { tab },
  })
}

onMounted(async () => {
  loading.value = true
  errorMessage.value = null
  try {
    const [t, s] = await Promise.all([listTemplates(), listServices()])
    templates.value = t
    services.value = s
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load catalog'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="mx-auto max-w-5xl animate-fade-up space-y-8 pb-12">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p class="lp-label mb-1">Golden paths</p>
        <h1 class="text-2xl font-semibold tracking-tight sm:text-3xl">Service catalog</h1>
        <p class="mt-2 max-w-2xl text-sm text-[var(--lp-muted)]">
          Org-approved stacks with owner, tier, SLO, and scorecards. One click scaffolds repo-ready
          Dockerfile, Kubernetes, CI/CD, and a workspace.
        </p>
      </div>
      <NuxtLink to="/catalog/create" class="lp-btn-primary text-xs uppercase tracking-wide">
        <span class="material-symbols-outlined text-base">add</span>
        Create service
      </NuxtLink>
    </header>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Loading catalog…</p>
    <p v-else-if="errorMessage" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>

    <template v-else>
      <div
        class="inline-flex rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-1"
        role="tablist"
        aria-label="Catalog sections"
      >
        <button
          type="button"
          role="tab"
          class="rounded-lg px-4 py-2 text-xs font-medium uppercase tracking-wide transition"
          :class="activeTab === 'templates'
            ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
            : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :aria-selected="activeTab === 'templates'"
          @click="setTab('templates')"
        >
          Approved Templates
          <span class="ml-1.5 font-mono text-[10px] opacity-70">{{ templates.length }}</span>
        </button>
        <button
          type="button"
          role="tab"
          class="rounded-lg px-4 py-2 text-xs font-medium uppercase tracking-wide transition"
          :class="activeTab === 'services'
            ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
            : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :aria-selected="activeTab === 'services'"
          @click="setTab('services')"
        >
          Your services
          <span class="ml-1.5 font-mono text-[10px] opacity-70">{{ services.length }}</span>
        </button>
      </div>

      <section v-if="activeTab === 'templates'" class="space-y-4" role="tabpanel">
        <h2 class="sr-only">Approved templates</h2>
        <div class="grid gap-4 sm:grid-cols-2">
          <article
            v-for="tpl in templates"
            :key="tpl.id"
            class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5"
          >
            <div class="flex items-start gap-3">
              <span class="material-symbols-outlined text-2xl text-[var(--lp-accent)]">{{ tpl.icon }}</span>
              <div class="min-w-0">
                <h3 class="font-semibold">{{ tpl.title }}</h3>
                <p class="mt-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                  {{ tpl.id }} · v{{ tpl.version }} · {{ tpl.default_tier }} · SLO {{ tpl.default_slo }}%
                </p>
                <p class="mt-2 text-sm text-[var(--lp-muted)]">{{ tpl.description }}</p>
                <div class="mt-3 space-y-2">
                  <div v-if="tpl.frameworks.length" class="space-y-1">
                    <p class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">Stacks</p>
                    <div class="flex flex-wrap gap-1">
                      <span
                        v-for="fw in tpl.frameworks"
                        :key="fw"
                        class="rounded border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-2 py-0.5 font-mono text-[10px] text-[var(--lp-accent)]"
                      >
                        {{ fw }}
                      </span>
                    </div>
                  </div>
                  <div v-if="tpl.docker_images?.length" class="space-y-1">
                    <p class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">Docker images</p>
                    <div class="flex flex-wrap gap-1">
                      <span
                        v-for="image in tpl.docker_images"
                        :key="image"
                        class="rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2 py-0.5 font-mono text-[10px] text-[var(--lp-text)]"
                      >
                        {{ image }}
                      </span>
                    </div>
                  </div>
                  <div class="flex flex-wrap gap-1">
                    <span
                      v-for="tag in tpl.tags"
                      :key="tag"
                      class="rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] text-[var(--lp-muted)]"
                    >
                      {{ tag }}
                    </span>
                  </div>
                </div>
                <NuxtLink
                  :to="`/catalog/create?template=${tpl.id}`"
                  class="mt-4 inline-flex lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
                >
                  Use template
                </NuxtLink>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section v-else class="space-y-4" role="tabpanel">
        <h2 class="sr-only">Your services</h2>
        <p v-if="!services.length" class="text-sm text-[var(--lp-muted)]">
          No services yet — create one from an approved golden path.
        </p>
        <div v-else class="space-y-3">
          <NuxtLink
            v-for="svc in services"
            :key="svc.id"
            :to="`/catalog/${svc.id}`"
            class="block rounded-xl border border-[var(--lp-line)] p-4 transition hover:border-[var(--lp-accent)]/40 hover:bg-[var(--lp-panel-2)]/40"
          >
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p class="font-semibold">{{ svc.name }}</p>
                <p class="mt-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                  {{ svc.tier }} · owner {{ svc.owner }} · SLO {{ svc.slo_target }}%
                </p>
              </div>
              <span
                class="rounded border px-2 py-1 font-mono text-[10px] uppercase"
                :class="svc.scorecard.passed
                  ? 'border-[var(--lp-ok)]/40 text-[var(--lp-ok)]'
                  : 'border-amber-500/40 text-amber-400'"
              >
                Score {{ svc.compliance_score }}
              </span>
            </div>
          </NuxtLink>
        </div>
      </section>
    </template>
  </div>
</template>
