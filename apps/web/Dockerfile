# Nuxt 4 production image (multi-arch: amd64 / arm64 for OCI Ampere A1)
# Requires BuildKit (DOCKER_BUILDKIT=1) for npm/font cache mounts.
FROM node:22-bookworm-slim AS build

WORKDIR /app

ENV NODE_ENV=production \
    NUXT_TELEMETRY_DISABLED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

COPY package.json package-lock.json ./

# Cache npm downloads across CI builds; skip lifecycle until sources are present.
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts

COPY . .

ARG NUXT_PUBLIC_API_BASE=/api/v1
ARG NUXT_PUBLIC_WS_BASE=
ENV NUXT_PUBLIC_API_BASE=${NUXT_PUBLIC_API_BASE} \
    NUXT_PUBLIC_WS_BASE=${NUXT_PUBLIC_WS_BASE}

# Reuse npm + Nuxt/font caches so rebuilds spend less time on install and Google fonts.
RUN --mount=type=cache,target=/root/.npm \
    --mount=type=cache,target=/app/node_modules/.cache \
    npx nuxt prepare && npm run build

FROM node:22-bookworm-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    NITRO_HOST=0.0.0.0 \
    NITRO_PORT=3000 \
    NUXT_TELEMETRY_DISABLED=1

COPY --from=build /app/.output ./.output

EXPOSE 3000

USER node

CMD ["node", ".output/server/index.mjs"]
