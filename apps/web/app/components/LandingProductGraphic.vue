<script setup lang="ts">
/**
 * Landing visual: how Launchpad sits between developers, git, and runtimes.
 * Decorative SVG (role=img); labels come from i18n for a11y via aria-labelledby.
 */
const { t } = useI18n()

const titleId = useId()
const descId = useId()
</script>

<template>
  <figure
    class="relative w-full overflow-hidden rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-4 sm:p-6"
    :aria-labelledby="titleId"
    :aria-describedby="descId"
  >
    <figcaption class="sr-only">
      <span :id="titleId">{{ t('landing.graphic.title') }}</span>
      <span :id="descId">{{ t('landing.graphic.description') }}</span>
    </figcaption>

    <!-- Ambient grid -->
    <div
      class="pointer-events-none absolute inset-0 opacity-[0.35]"
      aria-hidden="true"
      style="
        background-image:
          linear-gradient(var(--lp-grid) 1px, transparent 1px),
          linear-gradient(90deg, var(--lp-grid) 1px, transparent 1px);
        background-size: 28px 28px;
        mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, black 20%, transparent 75%);
      "
    />

    <svg
      class="relative z-[1] mx-auto h-auto w-full max-w-xl"
      viewBox="0 0 560 420"
      role="img"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient id="lp-hub-fill" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="var(--lp-accent-dim)" stop-opacity="0.55" />
          <stop offset="100%" stop-color="var(--lp-panel-2)" stop-opacity="0.95" />
        </linearGradient>
        <marker
          id="lp-arrow"
          markerWidth="8"
          markerHeight="8"
          refX="6"
          refY="3"
          orient="auto"
        >
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--lp-accent)" fill-opacity="0.85" />
        </marker>
      </defs>

      <!-- Flow connectors -->
      <g
        stroke="var(--lp-accent)"
        stroke-width="1.5"
        fill="none"
        stroke-opacity="0.55"
        marker-end="url(#lp-arrow)"
        class="animate-pulse-line"
      >
        <path d="M120 78 C160 78, 180 150, 220 190" />
        <path d="M440 78 C400 78, 380 150, 340 190" />
        <path d="M280 248 L160 310" />
        <path d="M280 248 L280 310" />
        <path d="M280 248 L400 310" />
      </g>

      <!-- Sources -->
      <g>
        <rect
          x="28"
          y="36"
          width="184"
          height="84"
          rx="14"
          fill="var(--lp-panel-2)"
          stroke="var(--lp-line)"
          stroke-width="1.5"
        />
        <text
          x="120"
          y="68"
          text-anchor="middle"
          fill="var(--lp-accent)"
          font-family="ui-monospace, monospace"
          font-size="11"
          letter-spacing="0.12em"
        >
          {{ t('landing.graphic.sourceLabel') }}
        </text>
        <text
          x="120"
          y="94"
          text-anchor="middle"
          fill="var(--lp-text)"
          font-size="15"
          font-weight="600"
        >
          {{ t('landing.graphic.sourceTitle') }}
        </text>
      </g>

      <g>
        <rect
          x="348"
          y="36"
          width="184"
          height="84"
          rx="14"
          fill="var(--lp-panel-2)"
          stroke="var(--lp-line)"
          stroke-width="1.5"
        />
        <text
          x="440"
          y="68"
          text-anchor="middle"
          fill="var(--lp-accent)"
          font-family="ui-monospace, monospace"
          font-size="11"
          letter-spacing="0.12em"
        >
          {{ t('landing.graphic.gitLabel') }}
        </text>
        <text
          x="440"
          y="94"
          text-anchor="middle"
          fill="var(--lp-text)"
          font-size="15"
          font-weight="600"
        >
          {{ t('landing.graphic.gitTitle') }}
        </text>
      </g>

      <!-- Control plane hub -->
      <g>
        <rect
          x="148"
          y="168"
          width="264"
          height="92"
          rx="18"
          fill="url(#lp-hub-fill)"
          stroke="var(--lp-accent)"
          stroke-width="2"
        />
        <text
          x="280"
          y="202"
          text-anchor="middle"
          fill="var(--lp-accent)"
          font-family="ui-monospace, monospace"
          font-size="11"
          letter-spacing="0.16em"
        >
          {{ t('landing.graphic.hubLabel') }}
        </text>
        <text
          x="280"
          y="230"
          text-anchor="middle"
          fill="var(--lp-text)"
          font-size="20"
          font-weight="700"
        >
          {{ t('brand.name') }}
        </text>
        <text
          x="280"
          y="248"
          text-anchor="middle"
          fill="var(--lp-muted)"
          font-size="12"
        >
          {{ t('landing.graphic.hubSubtitle') }}
        </text>
      </g>

      <!-- Targets -->
      <g>
        <rect
          x="28"
          y="318"
          width="132"
          height="78"
          rx="12"
          fill="var(--lp-panel-2)"
          stroke="var(--lp-line)"
          stroke-width="1.5"
        />
        <text
          x="94"
          y="350"
          text-anchor="middle"
          fill="var(--lp-text)"
          font-size="13"
          font-weight="600"
        >
          {{ t('landing.graphic.targetPreview') }}
        </text>
        <text
          x="94"
          y="372"
          text-anchor="middle"
          fill="var(--lp-muted)"
          font-size="11"
        >
          {{ t('landing.graphic.targetPreviewHint') }}
        </text>
      </g>

      <g>
        <rect
          x="214"
          y="318"
          width="132"
          height="78"
          rx="12"
          fill="var(--lp-panel-2)"
          stroke="var(--lp-line)"
          stroke-width="1.5"
        />
        <text
          x="280"
          y="350"
          text-anchor="middle"
          fill="var(--lp-text)"
          font-size="13"
          font-weight="600"
        >
          {{ t('landing.graphic.targetCloud') }}
        </text>
        <text
          x="280"
          y="372"
          text-anchor="middle"
          fill="var(--lp-muted)"
          font-size="11"
        >
          {{ t('landing.graphic.targetCloudHint') }}
        </text>
      </g>

      <g>
        <rect
          x="400"
          y="318"
          width="132"
          height="78"
          rx="12"
          fill="var(--lp-panel-2)"
          stroke="var(--lp-line)"
          stroke-width="1.5"
        />
        <text
          x="466"
          y="350"
          text-anchor="middle"
          fill="var(--lp-text)"
          font-size="13"
          font-weight="600"
        >
          {{ t('landing.graphic.targetHybrid') }}
        </text>
        <text
          x="466"
          y="372"
          text-anchor="middle"
          fill="var(--lp-muted)"
          font-size="11"
        >
          {{ t('landing.graphic.targetHybridHint') }}
        </text>
      </g>
    </svg>

    <!-- Pipeline strip -->
    <ol
      class="relative z-[1] mt-5 flex flex-wrap items-center justify-center gap-2 border-t border-[var(--lp-line)] pt-5 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--lp-muted)] sm:gap-3 sm:text-[11px]"
    >
      <li class="rounded-md border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-2.5 py-1.5 text-[var(--lp-text)]">
        {{ t('landing.graphic.stepLaunch') }}
      </li>
      <li class="text-[var(--lp-accent)]" aria-hidden="true">→</li>
      <li class="rounded-md border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-2.5 py-1.5 text-[var(--lp-text)]">
        {{ t('landing.graphic.stepProvision') }}
      </li>
      <li class="text-[var(--lp-accent)]" aria-hidden="true">→</li>
      <li class="rounded-md border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-2.5 py-1.5 text-[var(--lp-text)]">
        {{ t('landing.graphic.stepPreview') }}
      </li>
      <li class="text-[var(--lp-accent)]" aria-hidden="true">→</li>
      <li class="rounded-md border border-[var(--lp-accent)]/40 bg-[var(--lp-accent-dim)]/30 px-2.5 py-1.5 text-[var(--lp-accent)]">
        {{ t('landing.graphic.stepOperate') }}
      </li>
    </ol>
  </figure>
</template>
