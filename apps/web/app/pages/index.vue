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

    <header class="relative z-10 mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-6">
      <NuxtLink to="/" class="block">
        <BrandLogo size="sm" />
      </NuxtLink>
      <div class="flex items-center gap-3 sm:gap-4">
        <PreferenceControls compact />
        <nav class="flex items-center gap-4 text-sm">
          <NuxtLink
            to="/docs"
            class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
          >
            {{ t('landing.docs') }}
          </NuxtLink>
          <NuxtLink
            v-if="signedIn"
            to="/home"
            class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
          >
            {{ t('landing.openHub') }}
          </NuxtLink>
          <NuxtLink
            v-else
            to="/login"
            class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
          >
            {{ t('landing.signIn') }}
          </NuxtLink>
        </nav>
      </div>
    </header>

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
