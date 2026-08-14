<script setup lang="ts">
definePageMeta({
  layout: false,
})

const { t } = useI18n()
const { token, ready } = useAuth()

const signedIn = computed(() => Boolean(ready.value && token.value))

const primaryCta = computed(() =>
  signedIn.value
    ? { to: '/launch', label: t('landing.getStarted') }
    : { to: '/login?next=/launch', label: t('landing.getStarted') },
)

const hubLink = computed(() =>
  signedIn.value
    ? { to: '/home', label: t('landing.openHub') }
    : { to: '/login', label: t('landing.signIn') },
)

const mobileNavOpen = ref(false)

const platforms = computed(() => [
  { key: 'gcp', label: t('landing.platforms.gcp') },
  { key: 'aws', label: t('landing.platforms.aws') },
  { key: 'azure', label: t('landing.platforms.azure') },
  { key: 'cloudflare', label: t('landing.platforms.cloudflare') },
  { key: 'kubernetes', label: t('landing.platforms.kubernetes') },
  { key: 'github', label: t('landing.platforms.github') },
  { key: 'gitlab', label: t('landing.platforms.gitlab') },
  { key: 'terraform', label: t('landing.platforms.terraform') },
])

const capabilities = computed(() => [
  {
    key: 'previews',
    icon: 'rocket_launch',
    title: t('landing.capabilities.previews.title'),
    blurb: t('landing.capabilities.previews.blurb'),
  },
  {
    key: 'iac',
    icon: 'schema',
    title: t('landing.capabilities.iac.title'),
    blurb: t('landing.capabilities.iac.blurb'),
  },
  {
    key: 'k8s',
    icon: 'deployed_code',
    title: t('landing.capabilities.k8s.title'),
    blurb: t('landing.capabilities.k8s.blurb'),
  },
  {
    key: 'catalog',
    icon: 'inventory_2',
    title: t('landing.capabilities.catalog.title'),
    blurb: t('landing.capabilities.catalog.blurb'),
  },
  {
    key: 'hybrid',
    icon: 'hub',
    title: t('landing.capabilities.hybrid.title'),
    blurb: t('landing.capabilities.hybrid.blurb'),
  },
  {
    key: 'governance',
    icon: 'policy',
    title: t('landing.capabilities.governance.title'),
    blurb: t('landing.capabilities.governance.blurb'),
  },
])

const steps = computed(() => [
  {
    key: 'connect',
    n: '01',
    title: t('landing.steps.connect.title'),
    blurb: t('landing.steps.connect.blurb'),
  },
  {
    key: 'launch',
    n: '02',
    title: t('landing.steps.launch.title'),
    blurb: t('landing.steps.launch.blurb'),
  },
  {
    key: 'operate',
    n: '03',
    title: t('landing.steps.operate.title'),
    blurb: t('landing.steps.operate.blurb'),
  },
])

const stats = computed(() => [
  { key: 'clouds', value: t('landing.stats.clouds.value'), label: t('landing.stats.clouds.label') },
  { key: 'engines', value: t('landing.stats.engines.value'), label: t('landing.stats.engines.label') },
  { key: 'runtime', value: t('landing.stats.runtime.value'), label: t('landing.stats.runtime.label') },
  { key: 'gitops', value: t('landing.stats.gitops.value'), label: t('landing.stats.gitops.label') },
])

const securityPoints = computed(() => [
  t('landing.security.points.sso'),
  t('landing.security.points.vault'),
  t('landing.security.points.sandbox'),
  t('landing.security.points.audit'),
])

function closeMobileNav() {
  mobileNavOpen.value = false
}
</script>

<template>
  <div class="relative min-h-screen overflow-x-hidden bg-[var(--lp-ink)] text-[var(--lp-text)]">
    <div
      class="pointer-events-none absolute inset-x-0 top-0 h-[52rem] opacity-50"
      aria-hidden="true"
      style="
        background:
          radial-gradient(ellipse 90% 55% at 50% -15%, rgba(45, 212, 191, 0.2), transparent 58%),
          radial-gradient(ellipse 45% 35% at 95% 25%, rgba(15, 118, 110, 0.16), transparent 55%),
          radial-gradient(ellipse 35% 30% at 5% 40%, rgba(45, 212, 191, 0.08), transparent 50%);
      "
    />

    <header
      class="relative z-30 border-b border-[var(--lp-line)]/70 bg-[var(--lp-ink)]/80 backdrop-blur-md"
    >
      <div class="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3.5 sm:px-6">
        <NuxtLink to="/" class="min-w-0 shrink-0" @click="closeMobileNav">
          <span class="sm:hidden">
            <BrandLogo size="sm" :show-wordmark="false" />
          </span>
          <span class="hidden sm:block">
            <BrandLogo size="sm" />
          </span>
        </NuxtLink>

        <div class="flex min-w-0 shrink items-center gap-2 sm:gap-5">
          <PreferenceControls compact />
          <nav class="hidden items-center gap-5 text-sm lg:flex">
            <a
              href="#product"
              class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
            >
              {{ t('landing.nav.product') }}
            </a>
            <a
              href="#platforms"
              class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
            >
              {{ t('landing.nav.platforms') }}
            </a>
            <a
              href="#security"
              class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
            >
              {{ t('landing.nav.security') }}
            </a>
            <NuxtLink
              to="/docs"
              class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
            >
              {{ t('landing.docs') }}
            </NuxtLink>
            <NuxtLink
              :to="hubLink.to"
              class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
            >
              {{ hubLink.label }}
            </NuxtLink>
          </nav>
          <NuxtLink
            :to="primaryCta.to"
            class="lp-btn-primary hidden items-center gap-1.5 px-3.5 py-2 text-xs font-medium sm:inline-flex"
          >
            {{ primaryCta.label }}
          </NuxtLink>
          <button
            type="button"
            class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)] lg:hidden"
            :aria-expanded="mobileNavOpen"
            :aria-label="mobileNavOpen ? t('common.close') : t('shell.openNav')"
            @click="mobileNavOpen = !mobileNavOpen"
          >
            <span class="material-symbols-outlined text-[1.25rem]">
              {{ mobileNavOpen ? 'close' : 'menu' }}
            </span>
          </button>
        </div>
      </div>

      <div
        v-if="mobileNavOpen"
        class="border-t border-[var(--lp-line)] bg-[var(--lp-panel)]/95 px-4 py-3 backdrop-blur-md lg:hidden"
      >
        <nav class="mx-auto flex max-w-6xl flex-col gap-1 text-sm">
          <a
            href="#product"
            class="rounded-lg px-3 py-2.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            @click="closeMobileNav"
          >
            {{ t('landing.nav.product') }}
          </a>
          <a
            href="#platforms"
            class="rounded-lg px-3 py-2.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            @click="closeMobileNav"
          >
            {{ t('landing.nav.platforms') }}
          </a>
          <a
            href="#security"
            class="rounded-lg px-3 py-2.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            @click="closeMobileNav"
          >
            {{ t('landing.nav.security') }}
          </a>
          <NuxtLink
            to="/docs"
            class="rounded-lg px-3 py-2.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            @click="closeMobileNav"
          >
            {{ t('landing.docs') }}
          </NuxtLink>
          <NuxtLink
            :to="hubLink.to"
            class="rounded-lg px-3 py-2.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            @click="closeMobileNav"
          >
            {{ hubLink.label }}
          </NuxtLink>
          <NuxtLink
            :to="primaryCta.to"
            class="lp-btn-primary mt-2 inline-flex items-center justify-center gap-2 px-4 py-2.5 text-sm"
            @click="closeMobileNav"
          >
            {{ primaryCta.label }}
          </NuxtLink>
        </nav>
      </div>
    </header>

    <main class="relative z-10">
      <!-- Hero -->
      <section class="mx-auto max-w-6xl px-6 pb-16 pt-14 md:pb-24 md:pt-20">
        <div
          class="grid items-center gap-12 animate-fade-up lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-14"
        >
          <div class="max-w-xl space-y-7">
            <p class="font-mono text-xs uppercase tracking-[0.28em] text-[var(--lp-accent)]">
              {{ t('landing.eyebrow') }}
            </p>
            <div class="space-y-4">
              <div class="flex items-center gap-4">
                <BrandLogo size="lg" :show-wordmark="false" />
                <h1 class="text-5xl font-semibold tracking-tight text-[var(--lp-text)] md:text-6xl md:leading-[1.05]">
                  {{ t('landing.heroTitle') }}
                </h1>
              </div>
              <p class="text-2xl font-medium leading-snug tracking-tight text-[var(--lp-text)] md:text-3xl">
                {{ t('landing.heroHeadline') }}
              </p>
            </div>
            <p class="text-lg leading-relaxed text-[var(--lp-muted)] md:text-xl">
              {{ t('landing.heroBlurb') }}
            </p>
            <div class="flex flex-wrap items-center gap-3 pt-1">
              <NuxtLink
                :to="primaryCta.to"
                class="lp-btn-primary inline-flex items-center gap-2 px-6 py-3 text-sm font-medium transition hover:brightness-110"
              >
                {{ primaryCta.label }}
                <span class="material-symbols-outlined text-base">arrow_forward</span>
              </NuxtLink>
              <NuxtLink
                to="/docs"
                class="lp-btn-ghost inline-flex items-center gap-2 px-5 py-3 text-sm"
              >
                {{ t('landing.readDocs') }}
              </NuxtLink>
            </div>
            <p class="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--lp-muted)]">
              {{ t('landing.heroTrust') }}
            </p>
          </div>

          <div class="animate-fade-up [animation-delay:120ms]">
            <LandingProductGraphic />
          </div>
        </div>
      </section>

      <!-- Platforms -->
      <section
        id="platforms"
        class="border-y border-[var(--lp-line)] bg-[var(--lp-panel)]/40 py-10"
      >
        <div class="mx-auto max-w-6xl px-6">
          <p class="mb-6 text-center font-mono text-[11px] uppercase tracking-[0.22em] text-[var(--lp-muted)]">
            {{ t('landing.platforms.eyebrow') }}
          </p>
          <ul class="flex flex-wrap items-center justify-center gap-3 sm:gap-4">
            <li
              v-for="(p, i) in platforms"
              :key="p.key"
              class="animate-fade-up rounded-full border border-[var(--lp-line)] bg-[var(--lp-ink)]/50 px-4 py-2 font-mono text-xs tracking-wide text-[var(--lp-text)]"
              :style="{ animationDelay: `${i * 40}ms` }"
            >
              {{ p.label }}
            </li>
          </ul>
        </div>
      </section>

      <!-- Capabilities -->
      <section id="product" class="mx-auto max-w-6xl px-6 py-20 md:py-28">
        <div class="mx-auto max-w-2xl space-y-4 text-center">
          <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
            {{ t('landing.capabilities.eyebrow') }}
          </p>
          <h2 class="text-3xl font-semibold tracking-tight md:text-4xl">
            {{ t('landing.capabilities.title') }}
          </h2>
          <p class="text-base leading-relaxed text-[var(--lp-muted)] md:text-lg">
            {{ t('landing.capabilities.blurb') }}
          </p>
        </div>

        <div class="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <article
            v-for="(cap, index) in capabilities"
            :key="cap.key"
            class="group rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/70 p-6 transition hover:border-[var(--lp-accent)]/35 hover:bg-[var(--lp-panel-2)] animate-fade-up"
            :style="{ animationDelay: `${index * 60}ms` }"
          >
            <span
              class="material-symbols-outlined text-[1.75rem] text-[var(--lp-accent)] transition group-hover:scale-105"
            >
              {{ cap.icon }}
            </span>
            <h3 class="mt-4 text-lg font-semibold text-[var(--lp-text)]">
              {{ cap.title }}
            </h3>
            <p class="mt-2 text-sm leading-relaxed text-[var(--lp-muted)]">
              {{ cap.blurb }}
            </p>
          </article>
        </div>
      </section>

      <!-- How it works -->
      <section class="border-y border-[var(--lp-line)] bg-[var(--lp-panel)]/30 py-20 md:py-28">
        <div class="mx-auto max-w-6xl px-6">
          <div class="mx-auto max-w-2xl space-y-4 text-center">
            <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
              {{ t('landing.steps.eyebrow') }}
            </p>
            <h2 class="text-3xl font-semibold tracking-tight md:text-4xl">
              {{ t('landing.steps.title') }}
            </h2>
            <p class="text-base leading-relaxed text-[var(--lp-muted)]">
              {{ t('landing.steps.blurb') }}
            </p>
          </div>

          <ol class="mt-14 grid gap-6 md:grid-cols-3 md:gap-8">
            <li
              v-for="(step, index) in steps"
              :key="step.key"
              class="relative rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-6 animate-fade-up"
              :style="{ animationDelay: `${index * 80}ms` }"
            >
              <p class="font-mono text-sm text-[var(--lp-accent)]">{{ step.n }}</p>
              <h3 class="mt-3 text-xl font-semibold">{{ step.title }}</h3>
              <p class="mt-3 text-sm leading-relaxed text-[var(--lp-muted)]">{{ step.blurb }}</p>
            </li>
          </ol>
        </div>
      </section>

      <!-- Security -->
      <section id="security" class="mx-auto max-w-6xl px-6 py-20 md:py-28">
        <div class="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
          <div class="space-y-5">
            <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
              {{ t('landing.security.eyebrow') }}
            </p>
            <h2 class="text-3xl font-semibold tracking-tight md:text-4xl">
              {{ t('landing.security.title') }}
            </h2>
            <p class="text-base leading-relaxed text-[var(--lp-muted)] md:text-lg">
              {{ t('landing.security.blurb') }}
            </p>
            <NuxtLink
              to="/docs"
              class="inline-flex items-center gap-2 text-sm text-[var(--lp-accent)] hover:underline"
            >
              {{ t('landing.security.cta') }}
              <span class="material-symbols-outlined text-base">arrow_forward</span>
            </NuxtLink>
          </div>
          <ul class="grid gap-3 sm:grid-cols-2">
            <li
              v-for="(point, index) in securityPoints"
              :key="index"
              class="flex gap-3 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-4"
            >
              <span class="material-symbols-outlined shrink-0 text-[var(--lp-accent)]">
                verified_user
              </span>
              <span class="text-sm leading-relaxed text-[var(--lp-text)]">{{ point }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- Stats -->
      <section class="border-y border-[var(--lp-line)] bg-[var(--lp-panel)]/40 py-14">
        <div class="mx-auto grid max-w-6xl gap-8 px-6 sm:grid-cols-2 lg:grid-cols-4">
          <div
            v-for="stat in stats"
            :key="stat.key"
            class="text-center sm:text-left"
          >
            <p class="font-mono text-3xl font-semibold tracking-tight text-[var(--lp-accent)] md:text-4xl">
              {{ stat.value }}
            </p>
            <p class="mt-2 text-sm text-[var(--lp-muted)]">{{ stat.label }}</p>
          </div>
        </div>
      </section>

      <!-- Final CTA -->
      <section class="mx-auto max-w-6xl px-6 py-20 md:py-28">
        <div
          class="relative overflow-hidden rounded-3xl border border-[var(--lp-accent)]/30 bg-[var(--lp-panel)] px-8 py-12 text-center md:px-16 md:py-16"
        >
          <div
            class="pointer-events-none absolute inset-0 opacity-60"
            aria-hidden="true"
            style="
              background:
                radial-gradient(ellipse 60% 80% at 50% 120%, rgba(45, 212, 191, 0.18), transparent 60%);
            "
          />
          <div class="relative z-[1] mx-auto max-w-2xl space-y-5">
            <h2 class="text-3xl font-semibold tracking-tight md:text-4xl">
              {{ t('landing.finalCta.title') }}
            </h2>
            <p class="text-base leading-relaxed text-[var(--lp-muted)] md:text-lg">
              {{ t('landing.finalCta.blurb') }}
            </p>
            <div class="flex flex-wrap items-center justify-center gap-3 pt-2">
              <NuxtLink
                :to="primaryCta.to"
                class="lp-btn-primary inline-flex items-center gap-2 px-6 py-3 text-sm font-medium"
              >
                {{ primaryCta.label }}
                <span class="material-symbols-outlined text-base">arrow_forward</span>
              </NuxtLink>
              <NuxtLink
                to="/docs#getting-started"
                class="lp-btn-ghost inline-flex items-center gap-2 px-5 py-3 text-sm"
              >
                {{ t('landing.gettingStarted') }}
              </NuxtLink>
            </div>
          </div>
        </div>
      </section>
    </main>

    <footer class="relative z-10 border-t border-[var(--lp-line)] bg-[var(--lp-panel)]/50">
      <div class="mx-auto grid max-w-6xl gap-10 px-6 py-14 sm:grid-cols-2 lg:grid-cols-4">
        <div class="space-y-4 sm:col-span-2 lg:col-span-1">
          <BrandLogo size="sm" />
          <p class="max-w-xs text-sm leading-relaxed text-[var(--lp-muted)]">
            {{ t('landing.footer.blurb') }}
          </p>
        </div>
        <div>
          <p class="font-mono text-[11px] uppercase tracking-[0.2em] text-[var(--lp-muted)]">
            {{ t('landing.footer.product') }}
          </p>
          <ul class="mt-4 space-y-2 text-sm text-[var(--lp-muted)]">
            <li>
              <a href="#product" class="hover:text-[var(--lp-text)]">{{ t('landing.nav.product') }}</a>
            </li>
            <li>
              <a href="#platforms" class="hover:text-[var(--lp-text)]">{{ t('landing.nav.platforms') }}</a>
            </li>
            <li>
              <NuxtLink to="/launch" class="hover:text-[var(--lp-text)]">{{ t('landing.getStarted') }}</NuxtLink>
            </li>
          </ul>
        </div>
        <div>
          <p class="font-mono text-[11px] uppercase tracking-[0.2em] text-[var(--lp-muted)]">
            {{ t('landing.footer.resources') }}
          </p>
          <ul class="mt-4 space-y-2 text-sm text-[var(--lp-muted)]">
            <li>
              <NuxtLink to="/docs" class="hover:text-[var(--lp-text)]">{{ t('landing.docs') }}</NuxtLink>
            </li>
            <li>
              <NuxtLink to="/docs#getting-started" class="hover:text-[var(--lp-text)]">
                {{ t('landing.gettingStarted') }}
              </NuxtLink>
            </li>
            <li>
              <NuxtLink to="/docs" class="hover:text-[var(--lp-text)]">{{ t('landing.apiReference') }}</NuxtLink>
            </li>
          </ul>
        </div>
        <div>
          <p class="font-mono text-[11px] uppercase tracking-[0.2em] text-[var(--lp-muted)]">
            {{ t('landing.footer.access') }}
          </p>
          <ul class="mt-4 space-y-2 text-sm text-[var(--lp-muted)]">
            <li>
              <NuxtLink :to="hubLink.to" class="hover:text-[var(--lp-text)]">{{ hubLink.label }}</NuxtLink>
            </li>
            <li>
              <a href="#security" class="hover:text-[var(--lp-text)]">{{ t('landing.nav.security') }}</a>
            </li>
          </ul>
        </div>
      </div>
      <div
        class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 border-t border-[var(--lp-line)] px-6 py-6 text-xs text-[var(--lp-muted)]"
      >
        <p class="font-mono">{{ t('brand.product') }}</p>
        <p>{{ t('landing.footer.rights') }}</p>
      </div>
    </footer>
  </div>
</template>
