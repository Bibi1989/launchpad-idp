<script setup lang="ts">
const { t } = useI18n()

const sections = computed(() => [
  { id: 'overview', label: t('docs.howItWorks') },
  { id: 'getting-started', label: t('docs.gettingStarted') },
  { id: 'auth', label: t('docs.authAccess') },
  { id: 'home-settings', label: t('docs.homeSettings') },
  { id: 'catalog', label: t('docs.catalog') },
  { id: 'environments', label: t('docs.launchEnvironment') },
  { id: 'environment-ops', label: t('docs.environmentOps') },
  { id: 'rebuild', label: t('docs.gitPushRebuilds') },
  { id: 'provision', label: t('docs.provisionCloudInfra') },
  { id: 'provision-extras', label: t('docs.provisionExtras') },
  { id: 'hybrid', label: t('docs.hybridCloud') },
  { id: 'manifest', label: t('docs.applyManifests') },
  { id: 'projects', label: t('docs.projects') },
  { id: 'workspaces', label: t('docs.workspacesTerminal') },
  { id: 'dockerfiles', label: t('docs.dockerfilesGuide') },
  { id: 'organization', label: t('docs.organizationGuide') },
  { id: 'integrations', label: t('docs.integrationsGuide') },
  { id: 'network', label: t('docs.networkTopology') },
  { id: 'credentials', label: t('docs.cloudCredentials') },
  { id: 'github', label: t('docs.githubConnect') },
  { id: 'teardown', label: t('docs.teardownTtl') },
] as const)
</script>

<template>
  <div class="grid gap-8 lg:grid-cols-[220px_1fr] animate-fade-up">
    <aside class="lg:sticky lg:top-24 lg:self-start">
      <p class="lp-label mb-3">{{ t('docs.guide') }}</p>
      <nav class="space-y-1">
        <a
          v-for="section in sections"
          :key="section.id"
          :href="`#${section.id}`"
          class="lp-nav-link text-sm"
        >
          {{ section.label }}
        </a>
      </nav>
    </aside>

    <article class="min-w-0 space-y-12">
      <header class="space-y-3">
        <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">{{ t('docs.title') }}</p>
        <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">{{ t('docs.pageTitle') }}</h1>
        <p class="text-[var(--lp-muted)]">
          {{ t('docs.pageBlurb') }}
        </p>
        <i18n-t keypath="docs.moreGuides" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #productGuide>
            <code class="font-mono text-xs">{{ t('docs.productGuidePath') }}</code>
          </template>
        </i18n-t>
      </header>

      <section id="overview" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.howItWorks') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.overview.intro') }}
        </p>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <strong class="text-[var(--lp-text)]">{{ t('docs.provisionInfraLabel') }}</strong>
            - {{ t('docs.overview.provisionBody') }}
          </li>
          <li>
            <strong class="text-[var(--lp-text)]">{{ t('docs.manifestWorkloadLabel') }}</strong>
            -
            <i18n-t keypath="docs.overview.manifestBody" tag="span">
              <template #path>
                <code class="font-mono text-xs">infra/k8s/manifests/</code>
              </template>
            </i18n-t>
          </li>
          <li>
            <strong class="text-[var(--lp-text)]">{{ t('docs.environmentPreviewLabel') }}</strong>
            -
            <i18n-t keypath="docs.overview.environmentBody" tag="span">
              <template #manifestDeploy>
                <strong class="text-[var(--lp-text)]">{{ t('docs.manifestDeploy') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <strong class="text-[var(--lp-text)]">{{ t('docs.hybridCloudLabel') }}</strong>
            - {{ t('docs.overview.hybridBody') }}
          </li>
        </ul>
        <i18n-t keypath="docs.overview.useFlows" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #launch>
            <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.launch') }}</NuxtLink>
          </template>
          <template #environments>
            <NuxtLink to="/environments" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.environments') }}</NuxtLink>
          </template>
          <template #provision>
            <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.provision') }}</NuxtLink>
          </template>
          <template #workspaces>
            <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.workspaces') }}</NuxtLink>
          </template>
          <template #hybrid>
            <NuxtLink to="/fleet" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.hybrid') }}</NuxtLink>
          </template>
        </i18n-t>
      </section>

      <section id="getting-started" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.gettingStarted') }}</h2>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.gettingStartedSteps.step1') }}</li>
          <li>
            <i18n-t keypath="docs.gettingStartedSteps.step2" tag="span">
              <template #devLogin>
                <strong class="text-[var(--lp-text)]">{{ t('docs.devLogin') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.gettingStartedSteps.step3" tag="span">
              <template #home>
                <NuxtLink to="/home" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.home') }}</NuxtLink>
              </template>
              <template #root>
                <NuxtLink to="/" class="text-[var(--lp-accent)] hover:underline">/</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.gettingStartedSteps.step4" tag="span">
              <template #settings>
                <NuxtLink to="/settings" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.settings') }}</NuxtLink>
              </template>
            </i18n-t>
          </li>
        </ol>
      </section>

      <section id="auth" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.authAccess') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">{{ t('docs.authSection.intro') }}</p>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.authSection.login" tag="span">
              <template #loginPath>
                <NuxtLink to="/login" class="text-[var(--lp-accent)] hover:underline">/login</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.authSection.onboarding" tag="span">
              <template #onboardingPath>
                <NuxtLink to="/onboarding/org" class="text-[var(--lp-accent)] hover:underline">/onboarding/org</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.authSection.invites') }}</li>
        </ul>
      </section>

      <section id="home-settings" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.homeSettings') }}</h2>
        <i18n-t keypath="docs.homeSettingsSection.body" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #home>
            <NuxtLink to="/home" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.home') }}</NuxtLink>
          </template>
          <template #settings>
            <NuxtLink to="/settings" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.settings') }}</NuxtLink>
          </template>
        </i18n-t>
      </section>

      <section id="catalog" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.catalog') }}</h2>
        <i18n-t keypath="docs.catalogSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #catalog>
            <NuxtLink to="/catalog" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.catalogNav') }}</NuxtLink>
          </template>
        </i18n-t>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.catalogSection.step1" tag="span">
              <template #catalogPath>
                <NuxtLink to="/catalog" class="text-[var(--lp-accent)] hover:underline">/catalog</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.catalogSection.step2') }}</li>
          <li>
            <i18n-t keypath="docs.catalogSection.step3" tag="span">
              <template #launch>
                <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.launch') }}</NuxtLink>
              </template>
            </i18n-t>
          </li>
        </ol>
      </section>

      <section id="environments" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.launchEnvironment') }}</h2>
        <i18n-t keypath="docs.environmentsSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #launch>
            <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.launch') }}</NuxtLink>
          </template>
          <template #localSandbox>
            <strong class="text-[var(--lp-text)]">{{ t('docs.localSandbox') }}</strong>
          </template>
        </i18n-t>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.environmentsSection.step1" tag="span">
              <template #launchPath>
                <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">/launch</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.environmentsSection.step2" tag="span">
              <template #kindCmd>
                <code class="font-mono text-xs text-[var(--lp-text)]">make kind-down && make kind-up</code>
              </template>
              <template #k8sEnabled>
                <code class="font-mono text-xs text-[var(--lp-text)]">KUBERNETES_ENABLED=true</code>
              </template>
              <template #k8sContext>
                <code class="font-mono text-xs text-[var(--lp-text)]">KUBERNETES_CONTEXT=kind-launchpad</code>
              </template>
              <template #envFile>
                <code class="font-mono text-xs text-[var(--lp-text)]">.env</code>
              </template>
              <template #previewUrl>
                <code class="font-mono text-xs text-[var(--lp-text)]">http://127.0.0.1:&lt;nodePort&gt;</code>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.environmentsSection.step3') }}</li>
          <li>
            <i18n-t keypath="docs.environmentsSection.step4" tag="span">
              <template #running>
                <strong class="text-[var(--lp-text)]">{{ t('docs.running') }}</strong>
              </template>
              <template #openPreview>
                <strong class="text-[var(--lp-text)]">{{ t('docs.openPreview') }}</strong>
              </template>
            </i18n-t>
          </li>
        </ol>
      </section>

      <section id="environment-ops" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.environmentOps') }}</h2>
        <i18n-t keypath="docs.environmentOpsSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #environmentsPath>
            <NuxtLink to="/environments" class="text-[var(--lp-accent)] hover:underline">/environments</NuxtLink>
          </template>
        </i18n-t>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.environmentOpsSection.actions') }}</li>
          <li>{{ t('docs.environmentOpsSection.share') }}</li>
          <li>{{ t('docs.environmentOpsSection.caps') }}</li>
        </ul>
      </section>

      <section id="rebuild" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.gitPushRebuilds') }}</h2>
        <i18n-t keypath="docs.rebuildSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #webhookSecret>
            <code class="font-mono text-xs">WEBHOOK_SECRET</code>
          </template>
        </i18n-t>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.rebuildSection.step1') }}</li>
          <li>{{ t('docs.rebuildSection.step2') }}</li>
          <li>{{ t('docs.rebuildSection.step3') }}</li>
          <li>{{ t('docs.rebuildSection.step4') }}</li>
        </ol>
      </section>

      <section id="provision" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.provisionCloudInfra') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.provisionSection.intro') }}
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.provisionSection.step1" tag="span">
              <template #provision>
                <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.provision') }}</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.provisionSection.step2" tag="span">
              <template #terraform>
                <strong class="text-[var(--lp-text)]">{{ t('docs.terraformEngine') }}</strong>
              </template>
              <template #pulumi>
                <strong class="text-[var(--lp-text)]">{{ t('docs.pulumiEngine') }}</strong>
              </template>
              <template #ansible>
                <strong class="text-[var(--lp-text)]">{{ t('docs.ansibleEngine') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.provisionSection.step3" tag="span">
              <template #credentialsLink>
                <a href="#credentials" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.cloudCredentialsLink') }}</a>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.provisionSection.step4') }}</li>
          <li>
            <i18n-t keypath="docs.provisionSection.step5" tag="span">
              <template #rawK8s>
                <strong class="text-[var(--lp-text)]">{{ t('docs.rawK8sManifests') }}</strong>
              </template>
              <template #helm>
                <strong class="text-[var(--lp-text)]">{{ t('docs.helm') }}</strong>
              </template>
              <template #kustomize>
                <strong class="text-[var(--lp-text)]">{{ t('docs.kustomizeMode') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.provisionSection.step6') }}</li>
          <li>
            <i18n-t keypath="docs.provisionSection.step7" tag="span">
              <template #generateWorkspace>
                <strong class="text-[var(--lp-text)]">{{ t('docs.generateWorkspace') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.provisionSection.step8" tag="span">
              <template #tfPlan>
                <code class="text-[var(--lp-accent)]">terraform plan</code>
              </template>
              <template #tfApply>
                <code class="text-[var(--lp-accent)]">terraform apply</code>
              </template>
              <template #pulumiUp>
                <code class="text-[var(--lp-accent)]">pulumi up</code>
              </template>
              <template #kubectlApply>
                <code class="text-[var(--lp-accent)]">kubectl apply</code>
              </template>
              <template #helmUpgrade>
                <code class="text-[var(--lp-accent)]">helm upgrade</code>
              </template>
            </i18n-t>
          </li>
        </ol>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.provisionSection.credentialsNote') }}
        </p>
      </section>

      <section id="provision-extras" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.provisionExtras') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">{{ t('docs.provisionExtrasSection.intro') }}</p>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.provisionExtrasSection.ansible" tag="span">
              <template #ansible>
                <strong class="text-[var(--lp-text)]">{{ t('docs.ansibleEngine') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.provisionExtrasSection.kustomize" tag="span">
              <template #kustomize>
                <strong class="text-[var(--lp-text)]">{{ t('docs.kustomizeMode') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.provisionExtrasSection.scaffolds') }}</li>
          <li>{{ t('docs.provisionExtrasSection.import') }}</li>
        </ul>
      </section>

      <section id="hybrid" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.hybridCloud') }}</h2>
        <i18n-t keypath="docs.hybridSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #hybridPath>
            <NuxtLink to="/fleet" class="text-[var(--lp-accent)] hover:underline">/fleet</NuxtLink>
          </template>
          <template #docPath>
            <code class="font-mono text-xs">docs/hybrid-cloud.md</code>
          </template>
        </i18n-t>
        <h3 class="text-base font-semibold">{{ t('docs.hybridSection.whatTitle') }}</h3>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.hybridSection.whatAgents') }}</li>
          <li>{{ t('docs.hybridSection.whatAi') }}</li>
          <li>{{ t('docs.hybridSection.whatTargets') }}</li>
        </ul>
        <h3 class="text-base font-semibold">{{ t('docs.hybridSection.enrollTitle') }}</h3>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.hybridSection.enroll1" tag="span">
              <template #hybridPath>
                <NuxtLink to="/fleet" class="text-[var(--lp-accent)] hover:underline">/fleet</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.hybridSection.enroll2') }}</li>
          <li>{{ t('docs.hybridSection.enroll3') }}</li>
          <li>{{ t('docs.hybridSection.enroll4') }}</li>
        </ol>
        <h3 class="text-base font-semibold">{{ t('docs.hybridSection.aiTitle') }}</h3>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.hybridSection.ai1') }}</li>
          <li>{{ t('docs.hybridSection.ai2') }}</li>
          <li>{{ t('docs.hybridSection.ai3') }}</li>
        </ol>
        <h3 class="text-base font-semibold">{{ t('docs.hybridSection.securityTitle') }}</h3>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.hybridSection.securityBody') }}
        </p>
      </section>

      <section id="manifest" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.applyManifests') }}</h2>
        <i18n-t keypath="docs.manifestSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #rawK8s>
            <strong class="text-[var(--lp-text)]">{{ t('docs.rawK8sManifests') }}</strong>
          </template>
          <template #path>
            <code class="font-mono text-xs">infra/k8s/manifests/</code>
          </template>
        </i18n-t>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.manifestSection.step1') }}</li>
          <li>
            <i18n-t keypath="docs.manifestSection.step2" tag="span">
              <template #runKubectlOnOpen>
                <strong class="text-[var(--lp-text)]">{{ t('docs.runKubectlOnOpen') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.manifestSection.step3" tag="span">
              <template #launch>
                <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.launch') }}</NuxtLink>
              </template>
              <template #manifestDeploy>
                <strong class="text-[var(--lp-text)]">{{ t('docs.manifestDeploy') }}</strong>
              </template>
            </i18n-t>
          </li>
        </ol>
      </section>

      <section id="network" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.networkTopology') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.networkSection.intro') }}
        </p>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <strong class="text-[var(--lp-text)]">{{ t('docs.simple') }}</strong>
            - {{ t('docs.networkSection.simpleBody') }}
          </li>
          <li>
            <strong class="text-[var(--lp-text)]">{{ t('docs.standard') }}</strong>
            - {{ t('docs.networkSection.standardBody') }}
          </li>
        </ul>
      </section>

      <section id="credentials" class="scroll-mt-28 space-y-6">
        <div class="space-y-3">
          <h2 class="text-xl font-semibold">{{ t('docs.cloudCredentials') }}</h2>
          <p class="text-sm leading-7 text-[var(--lp-muted)]">
            {{ t('docs.credentialsSection.intro') }}
          </p>
          <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              <strong class="text-[var(--lp-text)]">{{ t('docs.accountSettings') }}</strong>
              (
              <NuxtLink to="/settings" class="text-[var(--lp-accent)] hover:underline">/settings</NuxtLink>
              ) - {{ t('docs.credentialsSection.accountSettingsBody') }}
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">{{ t('docs.perWorkspace') }}</strong>
              {{ t('docs.credentialsSection.perWorkspaceBody') }}
            </li>
          </ul>
          <p class="text-sm leading-7 text-[var(--lp-muted)]">
            {{ t('docs.credentialsSection.preferShortLived') }}
          </p>
        </div>

        <div id="gcp" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">{{ t('docs.gcpCredentials.title') }}</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              <i18n-t keypath="docs.gcpCredentials.step1" tag="span">
                <template #iamLink>
                  <a
                    class="text-[var(--lp-accent)] hover:underline"
                    href="https://console.cloud.google.com/iam-admin/serviceaccounts"
                    target="_blank"
                    rel="noreferrer"
                  >{{ t('docs.gcpCredentials.iamLinkLabel') }}</a>
                </template>
              </i18n-t>
            </li>
            <li>{{ t('docs.gcpCredentials.step2') }}</li>
            <li>{{ t('docs.gcpCredentials.step3') }}</li>
            <li>{{ t('docs.gcpCredentials.step4') }}</li>
            <li>
              <i18n-t keypath="docs.gcpCredentials.step5" tag="span">
                <template #gcpSaKeyJson>
                  <strong class="text-[var(--lp-text)]">{{ t('docs.gcpSaKeyJson') }}</strong>
                </template>
              </i18n-t>
            </li>
          </ol>
        </div>

        <div id="aws" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">{{ t('docs.awsCredentials.title') }}</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              <i18n-t keypath="docs.awsCredentials.step1" tag="span">
                <template #iamConsole>
                  <a
                    class="text-[var(--lp-accent)] hover:underline"
                    href="https://console.aws.amazon.com/iam/"
                    target="_blank"
                    rel="noreferrer"
                  >{{ t('docs.awsCredentials.iamConsoleLabel') }}</a>
                </template>
              </i18n-t>
            </li>
            <li>{{ t('docs.awsCredentials.step2') }}</li>
            <li>{{ t('docs.awsCredentials.step3') }}</li>
            <li>{{ t('docs.awsCredentials.step4') }}</li>
          </ol>
        </div>

        <div id="azure" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">{{ t('docs.azureCredentials.title') }}</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>{{ t('docs.azureCredentials.step1') }}</li>
            <li>{{ t('docs.azureCredentials.step2') }}</li>
            <li>{{ t('docs.azureCredentials.step3') }}</li>
            <li>{{ t('docs.azureCredentials.step4') }}</li>
            <li>{{ t('docs.azureCredentials.step5') }}</li>
          </ol>
        </div>

        <div id="cloudflare" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">{{ t('docs.cloudflareCredentials.title') }}</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              <i18n-t keypath="docs.cloudflareCredentials.step1" tag="span">
                <template #apiTokensLink>
                  <a
                    class="text-[var(--lp-accent)] hover:underline"
                    href="https://dash.cloudflare.com/profile/api-tokens"
                    target="_blank"
                    rel="noreferrer"
                  >{{ t('docs.cloudflareCredentials.apiTokensLinkLabel') }}</a>
                </template>
              </i18n-t>
            </li>
            <li>{{ t('docs.cloudflareCredentials.step2') }}</li>
            <li>{{ t('docs.cloudflareCredentials.step3') }}</li>
          </ol>
        </div>
      </section>

      <section id="github" class="scroll-mt-28 space-y-4">
        <h2 class="text-xl font-semibold">{{ t('docs.githubConnect') }}</h2>
        <i18n-t keypath="docs.githubSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #githubApp>
            <strong class="text-[var(--lp-text)]">{{ t('docs.githubApp') }}</strong>
          </template>
        </i18n-t>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.githubSection.step1" tag="span">
              <template #integrationsGithub>
                <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.integrationsGithub') }}</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.githubSection.step2" tag="span">
              <template #connectGithub>
                <strong class="text-[var(--lp-text)]">{{ t('docs.connectGithub') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.githubSection.step3') }}</li>
          <li>{{ t('docs.githubSection.step4') }}</li>
          <li>{{ t('docs.githubSection.step5') }}</li>
        </ol>
        <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/50 p-4 text-sm text-[var(--lp-muted)]">
          <p class="lp-label mb-2">{{ t('docs.githubSection.appNeedsTitle') }}</p>
          <ul class="mb-3 list-disc space-y-1 pl-5 text-xs leading-6">
            <li>
              <strong class="text-[var(--lp-text)]">{{ t('docs.administration') }}</strong>
              - {{ t('docs.githubSection.permAdministration') }}
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">{{ t('docs.contents') }}</strong>
              - {{ t('docs.githubSection.permContents') }}
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">{{ t('docs.secrets') }}</strong>
              - {{ t('docs.githubSection.permSecrets') }}
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">{{ t('docs.metadata') }}</strong>
              - {{ t('docs.githubSection.permMetadata') }}
            </li>
          </ul>
          <i18n-t keypath="docs.githubSection.afterPermissions" tag="p" class="text-xs leading-6">
            <template #accept>
              <strong class="text-[var(--lp-text)]">{{ t('docs.accept') }}</strong>
            </template>
            <template #personalAccount>
              <strong class="text-[var(--lp-text)]">{{ t('docs.personalAccount') }}</strong>
            </template>
            <template #organization>
              <strong class="text-[var(--lp-text)]">{{ t('docs.organization') }}</strong>
            </template>
          </i18n-t>
        </div>
        <GithubConnectCard compact />
      </section>

      <section id="workspaces" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.workspacesTerminal') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.workspacesSection.intro') }}
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.workspacesSection.step1" tag="span">
              <template #workspaces>
                <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">{{ t('docs.workspaces') }}</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.workspacesSection.step2" tag="span">
              <template #saveShortcut>
                <kbd class="font-mono text-[var(--lp-accent)]">⌘/Ctrl+S</kbd>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.workspacesSection.step3') }}</li>
          <li>
            <i18n-t keypath="docs.workspacesSection.step4" tag="span">
              <template #pushToGithub>
                <strong class="text-[var(--lp-text)]">{{ t('docs.pushToGithub') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>
            <i18n-t keypath="docs.workspacesSection.step5" tag="span">
              <template #kubectlApplyDelete>
                <strong class="text-[var(--lp-text)]">{{ t('docs.kubectlApplyDelete') }}</strong>
              </template>
              <template #planApplyDestroy>
                <strong class="text-[var(--lp-text)]">{{ t('docs.planApplyDestroy') }}</strong>
              </template>
            </i18n-t>
          </li>
        </ol>
      </section>

      <section id="projects" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.projects') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">{{ t('docs.projectsSection.intro') }}</p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.projectsSection.step1" tag="span">
              <template #projectsPath>
                <NuxtLink to="/projects" class="text-[var(--lp-accent)] hover:underline">/projects</NuxtLink>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.projectsSection.step2') }}</li>
          <li>{{ t('docs.projectsSection.step3') }}</li>
        </ol>
      </section>

      <section id="dockerfiles" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.dockerfilesGuide') }}</h2>
        <i18n-t keypath="docs.dockerfilesSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #dockerfilesPath>
            <NuxtLink to="/dockerfiles" class="text-[var(--lp-accent)] hover:underline">/dockerfiles</NuxtLink>
          </template>
        </i18n-t>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.dockerfilesSection.step1') }}</li>
          <li>{{ t('docs.dockerfilesSection.step2') }}</li>
          <li>{{ t('docs.dockerfilesSection.step3') }}</li>
        </ol>
      </section>

      <section id="organization" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.organizationGuide') }}</h2>
        <i18n-t keypath="docs.orgSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #orgPath>
            <NuxtLink to="/org" class="text-[var(--lp-accent)] hover:underline">/org</NuxtLink>
          </template>
        </i18n-t>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.orgSection.members') }}</li>
          <li>{{ t('docs.orgSection.sso') }}</li>
          <li>{{ t('docs.orgSection.billing') }}</li>
        </ul>
      </section>

      <section id="integrations" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.integrationsGuide') }}</h2>
        <i18n-t keypath="docs.integrationsSection.intro" tag="p" class="text-sm leading-7 text-[var(--lp-muted)]">
          <template #integrationsPath>
            <NuxtLink to="/integrations" class="text-[var(--lp-accent)] hover:underline">/integrations</NuxtLink>
          </template>
        </i18n-t>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>{{ t('docs.integrationsSection.github') }}</li>
          <li>{{ t('docs.integrationsSection.gitlab') }}</li>
          <li>{{ t('docs.integrationsSection.slack') }}</li>
          <li>{{ t('docs.integrationsSection.jira') }}</li>
        </ul>
      </section>

      <section id="teardown" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">{{ t('docs.teardownTtl') }}</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          {{ t('docs.teardownSection.intro') }}
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <i18n-t keypath="docs.teardownSection.step1" tag="span">
              <template #destroy>
                <strong class="text-[var(--lp-text)]">{{ t('common.destroy') }}</strong>
              </template>
            </i18n-t>
          </li>
          <li>{{ t('docs.teardownSection.step2') }}</li>
          <li>{{ t('docs.teardownSection.step3') }}</li>
        </ol>
      </section>
    </article>
  </div>
</template>
