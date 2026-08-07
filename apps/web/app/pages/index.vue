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

const features = computed(() => [
  {
    key: 'previews',
    title: t('landing.features.previews.title'),
    blurb: t('landing.features.previews.blurb'),
  },
  {
    key: 'iac',
    title: t('landing.features.iac.title'),
    blurb: t('landing.features.iac.blurb'),
  },
  {
    key: 'k8s',
    title: t('landing.features.k8s.title'),
    blurb: t('landing.features.k8s.blurb'),
  },
])

const mobileNavOpen = ref(false)

const hubLink = computed(() =>
  signedIn.value
    ? { to: '/home', label: t('landing.openHub') }
    : { to: '/login', label: t('landing.signIn') },
)

function closeMobileNav() {
  mobileNavOpen.value = false
}
</script>

<template>
  <div class="relative min-h-screen overflow-hidden">
    <div
      class="pointer-events-none absolute inset-0 opacity-40"
      aria-hidden="true"
      style="
        background:
          radial-gradient(ellipse 80% 50% at 50% -20%, rgba(45, 212, 191, 0.22), transparent 55%),
          radial-gradient(ellipse 40% 30% at 90% 60%, rgba(15, 118, 110, 0.15), transparent 50%);
      "
    />

    <header class="relative z-20 mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-4 sm:gap-4 sm:px-6 sm:py-6">
      <NuxtLink to="/" class="min-w-0 shrink-0" @click="closeMobileNav">
        <span class="sm:hidden">
          <BrandLogo size="sm" :show-wordmark="false" />
        </span>
        <span class="hidden sm:block">
          <BrandLogo size="sm" />
        </span>
      </NuxtLink>
      <div class="flex min-w-0 shrink items-center gap-2 sm:gap-4">
        <PreferenceControls compact />
        <nav class="hidden items-center gap-4 text-sm sm:flex">
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
        <button
          type="button"
          class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)] sm:hidden"
          :aria-expanded="mobileNavOpen"
          :aria-label="mobileNavOpen ? t('common.close') : t('shell.openNav')"
          @click="mobileNavOpen = !mobileNavOpen"
        >
          <span class="material-symbols-outlined text-[1.25rem]">
            {{ mobileNavOpen ? 'close' : 'menu' }}
          </span>
        </button>
      </div>
    </header>

    <div
      v-if="mobileNavOpen"
      class="relative z-20 border-b border-[var(--lp-line)] bg-[var(--lp-panel)]/95 px-4 py-3 backdrop-blur-md sm:hidden"
    >
      <nav class="mx-auto flex max-w-6xl flex-col gap-1 text-sm">
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
      </nav>
    </div>

    <main class="relative z-10 mx-auto flex max-w-6xl flex-col px-6 pb-24 pt-16 md:pt-24">
      <section class="max-w-3xl animate-fade-up space-y-8">
        <p class="font-mono text-xs uppercase tracking-[0.28em] text-[var(--lp-accent)]">
          {{ t('landing.eyebrow') }}
        </p>
        <div class="flex items-center gap-4">
          <BrandLogo size="lg" :show-wordmark="false" />
          <h1 class="text-5xl font-semibold tracking-tight text-[var(--lp-text)] md:text-6xl md:leading-[1.05]">
            {{ t('landing.heroTitle') }}
          </h1>
        </div>
        <p class="max-w-xl text-lg leading-relaxed text-[var(--lp-muted)] md:text-xl">
          {{ t('landing.heroBlurb') }}
        </p>
        <div class="flex flex-wrap items-center gap-4 pt-2">
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
      </section>

      <section class="mt-24 grid gap-10 border-t border-[var(--lp-line)] pt-16 md:grid-cols-3 md:gap-12">
        <div
          v-for="(feature, index) in features"
          :key="feature.key"
          class="animate-fade-up space-y-3"
          :style="{ animationDelay: `${(index + 1) * 80}ms` }"
        >
          <h2 class="text-lg font-semibold text-[var(--lp-text)]">{{ feature.title }}</h2>
          <p class="text-sm leading-relaxed text-[var(--lp-muted)]">{{ feature.blurb }}</p>
        </div>
      </section>
    </main>

    <footer class="relative z-10 mx-auto max-w-6xl border-t border-[var(--lp-line)] px-6 py-8">
      <div class="flex flex-wrap items-center justify-between gap-4 text-sm text-[var(--lp-muted)]">
        <p class="font-mono text-xs">{{ t('brand.product') }}</p>
        <div class="flex gap-6">
          <NuxtLink to="/docs#getting-started" class="hover:text-[var(--lp-text)]">
            {{ t('landing.gettingStarted') }}
          </NuxtLink>
          <a
            href="/docs"
            class="hover:text-[var(--lp-text)]"
          >
            {{ t('landing.apiReference') }}
          </a>
        </div>
      </div>
    </footer>
  </div>
</template>
