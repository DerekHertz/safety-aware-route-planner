# syntax=docker/dockerfile:1
#
# The reference client as a production container, for compose.yaml at the
# repo root. Build context is web/:
#
#     docker build -t sr-web web
#
# next.config.ts is read at BUILD time and serialized into the standalone
# server, and NEXT_PUBLIC_* values are inlined into the browser bundle then
# too. Every setting below is therefore a build arg; changing one means
# rebuilding this image (`docker compose build web`), not restarting it.

FROM node:22-slim AS builder
WORKDIR /web

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

# Where the Next server forwards /api/* and /commute/* (the service names in
# compose.yaml), and what the browser bundle is told. The commute URL is
# relative on purpose: same-origin through the /commute proxy, so one
# hostname serves the whole app and no CORS is involved.
ARG API_PROXY_TARGET=http://api:8080
ARG COMMUTE_PROXY_TARGET=http://commute:8100
ARG NEXT_PUBLIC_COMMUTE_URL=/commute
ARG NEXT_PUBLIC_ENABLE_LIVE_NAV=1
ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    COMMUTE_PROXY_TARGET=$COMMUTE_PROXY_TARGET \
    NEXT_PUBLIC_COMMUTE_URL=$NEXT_PUBLIC_COMMUTE_URL \
    NEXT_PUBLIC_ENABLE_LIVE_NAV=$NEXT_PUBLIC_ENABLE_LIVE_NAV \
    NEXT_OUTPUT=standalone \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ---------------------------------------------------------------------------
# Runtime: the traced standalone server only, no node_modules install.
# ---------------------------------------------------------------------------
FROM node:22-slim
WORKDIR /web

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The standalone server does not serve public/ or .next/static by itself;
# they are copied beside it, where server.js picks them up.
COPY --from=builder --chown=node:node /web/.next/standalone ./
COPY --from=builder --chown=node:node /web/.next/static ./.next/static
COPY --from=builder --chown=node:node /web/public ./public

USER node
EXPOSE 3000
CMD ["node", "server.js"]
