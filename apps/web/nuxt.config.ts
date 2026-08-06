// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-15",
  devtools: { enabled: true },
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
    families: [
      { name: "Sora", provider: "google" },
      { name: "IBM Plex Mono", provider: "google" },
    ],
  },
  runtimeConfig: {
    public: {
      // Same-origin proxy in dev avoids browser CORS for REST; override for direct API access.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || "/api/v1",
      // WebSockets must hit the API directly - Nitro/Vite proxies often drop WS upgrades.
      wsBase: process.env.NUXT_PUBLIC_WS_BASE || "ws://localhost:8000",
    },
  },
  nitro: {
    devProxy: {
      "/api/v1": {
        target: "http://localhost:8000/api/v1",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  vite: {
    server: {
      allowedHosts: [".trycloudflare.com"],
      proxy: {
        "/api/v1": {
          target: "http://localhost:8000",
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
      ],
      link: [
        { rel: "icon", type: "image/svg+xml", href: "/favicon.svg" },
        { rel: "icon", type: "image/x-icon", href: "/favicon.ico" },
        { rel: "icon", type: "image/png", sizes: "32x32", href: "/favicon-32.png" },
        { rel: "icon", type: "image/png", sizes: "16x16", href: "/favicon-16.png" },
        { rel: "apple-touch-icon", sizes: "180x180", href: "/apple-touch-icon.png" },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=swap",
        },
      ],
    },
  },
});
