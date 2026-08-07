# Nuxt 4 production image (multi-arch: amd64 / arm64)
# Use `npm install` (not `npm ci`): host may generate lockfiles with a newer npm
# (e.g. npm 11 on Node 26) than node:22-bookworm-slim (npm 10), and `npm ci`
# then fails with "package.json and package-lock.json are not in sync".
FROM node:22-bookworm-slim AS build

WORKDIR /app

ENV NUXT_TELEMETRY_DISABLED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

COPY package.json package-lock.json ./
RUN npm install --ignore-scripts

COPY . .

ARG NUXT_PUBLIC_API_BASE=/api/v1
ARG NUXT_PUBLIC_WS_BASE=
ENV NUXT_PUBLIC_API_BASE=${NUXT_PUBLIC_API_BASE} \
    NUXT_PUBLIC_WS_BASE=${NUXT_PUBLIC_WS_BASE} \
    NODE_ENV=production

RUN npx nuxt prepare && npm run build

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
