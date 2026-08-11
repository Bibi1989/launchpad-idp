// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-15",
  // Skip DevTools overhead in production Docker builds.
  devtools: { enabled: process.env.NODE_ENV !== "production" },
  modules: ["@nuxtjs/tailwindcss", "@nuxt/fonts", "@nuxtjs/i18n"],
  css: ["~/assets/css/main.css"],
  i18n: {
    locales: [
      { code: "de", language: "de-DE", name: "Deutsch", file: "de.ts" },
      { code: "en", language: "en-US", name: "English", file: "en.ts" },
    ],
    lazy: true,
    langDir: "locales",
    defaultLocale: "de",
    strategy: "no_prefix",
    // Custom plugin: user localStorage preference > browser language > German.
    detectBrowserLanguage: false,
    bundle: {
      optimizeTranslationDirective: false,
    },
  },
  fonts: {
    // CI/Docker often cannot resolve fonts.bunny.net; we only use Google families.
    provider: "google",
    providers: {
      bunny: false,
      adobe: false,
      fontshare: false,
    },
    defaults: {
      weights: [400, 500, 600, 700],
      styles: ["normal"],
      subsets: ["latin"],
    },
    families: [
      { name: "Sora", provider: "google", weights: [400, 600, 700] },
      { name: "IBM Plex Mono", provider: "google", weights: [400, 500] },
    ],
  },
  runtimeConfig: {
    public: {
      // Same-origin proxy in dev avoids browser CORS for REST; override for direct API access.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || "/api/v1",
      // Prefer 127.0.0.1 over localhost: on macOS localhost often resolves to ::1 first.
      // Docker Desktop may still bind *:8000 on IPv6 after a compose stack stops, which
      // makes Nuxt's proxy get ECONNRESET while local `make api` (IPv4-only) is healthy.
      wsBase: process.env.NUXT_PUBLIC_WS_BASE || "ws://127.0.0.1:8000",
    },
  },
  nitro: {
    minify: true,
    sourceMap: false,
    devProxy: {
      "/api/v1": {
        target: "http://127.0.0.1:8000/api/v1",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  vite: {
    build: {
      sourcemap: false,
      cssMinify: true,
    },
    server: {
      allowedHosts: [".trycloudflare.com"],
      proxy: {
        "/api/v1": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
          ws: true,
        },
      },
    },
    // Mermaid imports `dayjs` as ESM default; the CJS build breaks Vite 8 without this.
    resolve: {
      alias: [
        {
          find: /^dayjs$/,
          replacement: "dayjs/esm/index.js",
        },
      ],
    },
    optimizeDeps: {
      include: [
        "monaco-editor",
        "mermaid",
        "dayjs",
        "@braintree/sanitize-url",
        "cytoscape",
        "cytoscape-cose-bilkent",
        "cytoscape-fcose",
        "khroma",
        "debug",
        "lodash-es",
      ],
    },
    worker: {
      format: "es",
    },
  },
  // Mermaid pulls browser-only deps; keep it out of the SSR bundle.
  build: {
    transpile: ["mermaid", "dayjs"],
  },
  typescript: {
    strict: true,
    typeCheck: false,
  },
  hooks: {
    // Rolldown (Vite 8) emits PLUGIN_TIMINGS on large Monaco/Mermaid builds.
    "vite:extendConfig"(config) {
      const build = (config.build ??= {}) as Record<string, unknown>
      const existing = (build.rolldownOptions ?? {}) as Record<string, unknown>
      const checks = (existing.checks ?? {}) as Record<string, unknown>
      build.rolldownOptions = {
        ...existing,
        checks: { ...checks, pluginTimings: false },
      }
    },
  },
  app: {
    head: {
      title: "Launchpad",
      titleTemplate: "%s · Launchpad",
      meta: [
        {
          name: "description",
          content:
            "Internal developer portal for ephemeral environment provisioning",
        },
        { name: "theme-color", content: "#0c1219" },
        { property: "og:title", content: "Launchpad" },
        {
          property: "og:description",
          content:
            "Internal developer portal for ephemeral environment provisioning",
        },
        { property: "og:image", content: "/logo-512.png" },
        { name: "twitter:card", content: "summary" },
        { name: "twitter:image", content: "/logo-512.png" },
      ],
      script: [
        {
          // Apply stored theme before first paint so the splash screen matches.
          innerHTML:
            "(function(){try{var t=localStorage.getItem('lp_theme');if(t!=='light'&&t!=='dark')t='dark';var r=document.documentElement;r.dataset.theme=t;r.classList.toggle('dark',t==='dark');r.classList.toggle('light',t==='light');var m=document.querySelector('meta[name=\"theme-color\"]');if(m)m.setAttribute('content',t==='light'?'#ffffff':'#0c1219');}catch(e){}})();",
          tagPosition: "head",
        },
        {
          // Mark when Material Symbols is ready so icon ligature text stays hidden
          // until the icon font can render (prevents oversized fallback words).
          innerHTML:
            "(function(){function ready(){document.documentElement.classList.add('fonts-icons-ready')}try{if(document.fonts&&document.fonts.load){document.fonts.load('24px \"Material Symbols Outlined\"').then(ready).catch(ready);document.fonts.ready.then(ready).catch(ready);setTimeout(ready,1500)}else{ready()}}catch(e){ready()}})();",
          tagPosition: "head",
        },
      ],
      link: [
        { rel: "icon", type: "image/svg+xml", href: "/favicon.svg" },
        { rel: "icon", type: "image/x-icon", href: "/favicon.ico" },
        { rel: "icon", type: "image/png", sizes: "32x32", href: "/favicon-32.png" },
        { rel: "icon", type: "image/png", sizes: "16x16", href: "/favicon-16.png" },
        { rel: "apple-touch-icon", sizes: "180x180", href: "/apple-touch-icon.png" },
        {
          rel: "preload",
          as: "style",
          href: "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=block",
        },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=block",
        },
      ],
    },
  },
});
