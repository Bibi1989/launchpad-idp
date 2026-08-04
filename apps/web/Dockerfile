# Nuxt 4 production image (multi-arch: amd64 / arm64 for OCI Ampere A1)
FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

# Same-origin defaults - Caddy proxies /api/v1 and WebSockets to the API.
ARG NUXT_PUBLIC_API_BASE=/api/v1
ARG NUXT_PUBLIC_WS_BASE=
ENV NUXT_PUBLIC_API_BASE=${NUXT_PUBLIC_API_BASE}
ENV NUXT_PUBLIC_WS_BASE=${NUXT_PUBLIC_WS_BASE}
ENV NUXT_TELEMETRY_DISABLED=1

RUN npm run build

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
