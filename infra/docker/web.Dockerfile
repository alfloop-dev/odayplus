# syntax=docker/dockerfile:1
# ODay Plus web (Next.js, npm workspaces). Multi-stage: build then run the
# self-contained standalone server (next.config.mjs -> output: "standalone").
FROM node:22-slim AS builder
WORKDIR /app

# Install workspace deps first (better layer caching). Root manifest + lockfile
# plus every workspace package.json the install graph needs.
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages ./packages
RUN npm ci

# Build the web workspace.
COPY . .
ARG ODP_API_BASE_URL
ARG ODAY_RELEASE_SHA
ARG ODP_REQUIRE_LIVE_DATA
ARG ODP_DATA_BINDING_MODE
ARG ODP_PRODUCT_MODE
ENV ODP_API_BASE_URL=$ODP_API_BASE_URL \
    NEXT_PUBLIC_ODP_API_BASE_URL=$ODP_API_BASE_URL \
    ODAY_RELEASE_SHA=$ODAY_RELEASE_SHA \
    NEXT_PUBLIC_ODAY_RELEASE_SHA=$ODAY_RELEASE_SHA \
    ODP_REQUIRE_LIVE_DATA=$ODP_REQUIRE_LIVE_DATA \
    ODP_DATA_BINDING_MODE=$ODP_DATA_BINDING_MODE \
    NEXT_PUBLIC_ODP_DATA_BINDING_MODE=$ODP_DATA_BINDING_MODE \
    ODP_PRODUCT_MODE=$ODP_PRODUCT_MODE \
    NEXT_PUBLIC_ODP_PRODUCT_MODE=$ODP_PRODUCT_MODE
RUN npm run build --workspace=@oday-plus/web

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

ARG ODAY_RELEASE_SHA
ARG ODP_REQUIRE_LIVE_DATA
ARG ODP_DATA_BINDING_MODE
ARG ODP_PRODUCT_MODE
ENV ODAY_RELEASE_SHA=$ODAY_RELEASE_SHA \
    NEXT_PUBLIC_ODAY_RELEASE_SHA=$ODAY_RELEASE_SHA \
    ODP_REQUIRE_LIVE_DATA=$ODP_REQUIRE_LIVE_DATA \
    ODP_DATA_BINDING_MODE=$ODP_DATA_BINDING_MODE \
    NEXT_PUBLIC_ODP_DATA_BINDING_MODE=$ODP_DATA_BINDING_MODE \
    ODP_PRODUCT_MODE=$ODP_PRODUCT_MODE \
    NEXT_PUBLIC_ODP_PRODUCT_MODE=$ODP_PRODUCT_MODE

# Standalone output carries its own minimal node_modules + server.js,
# laid out under the monorepo path apps/web/.
COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static

EXPOSE 3000
CMD ["node", "apps/web/server.js"]
