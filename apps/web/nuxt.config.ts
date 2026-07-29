// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  modules: ['@nuxtjs/tailwindcss', '@nuxt/fonts'],
  css: ['~/assets/css/main.css'],
  fonts: {
    families: [
      { name: 'Sora', provider: 'google' },
      { name: 'IBM Plex Mono', provider: 'google' },
    ],
  },
  runtimeConfig: {
    public: {
      // Same-origin proxy in dev avoids browser CORS for REST; override for direct API access.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || '/api/v1',
      // WebSockets must hit the API directly — Nitro/Vite proxies often drop WS upgrades.
      wsBase: process.env.NUXT_PUBLIC_WS_BASE || 'ws://localhost:8000',
    },
  },
  nitro: {
    devProxy: {
      '/api/v1': {
        target: 'http://localhost:8000/api/v1',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  vite: {
    server: {
      proxy: {
        '/api/v1': {
          target: 'http://localhost:8000',
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
          replacement: 'dayjs/esm/index.js',
        },
      ],
    },
    optimizeDeps: {
      include: [
        'monaco-editor',
        'mermaid',
        'dayjs',
        '@braintree/sanitize-url',
        'cytoscape',
        'cytoscape-cose-bilkent',
        'cytoscape-fcose',
        'khroma',
        'debug',
        'lodash-es',
      ],
    },
    worker: {
      format: 'es',
    },
  },
  // Mermaid pulls browser-only deps; keep it out of the SSR bundle.
  build: {
    transpile: ['mermaid', 'dayjs'],
  },
  typescript: {
    strict: true,
    typeCheck: false,
  },
  app: {
    head: {
      title: 'Launchpad',
      meta: [
        {
          name: 'description',
          content: 'Internal developer portal for ephemeral environment provisioning',
        },
      ],
      link: [
        {
          rel: 'stylesheet',
          href: 'https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=swap',
        },
      ],
    },
  },
})
