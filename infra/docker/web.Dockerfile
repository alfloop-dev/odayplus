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
RUN npm run build --workspace=@oday-plus/web

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Standalone output carries its own minimal node_modules + server.js,
# laid out under the monorepo path apps/web/.
COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static

EXPOSE 3000
CMD ["node", "apps/web/server.js"]
