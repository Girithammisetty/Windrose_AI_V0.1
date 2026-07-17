# ui-web — Next.js 15 (Node 20 + pnpm). Multi-stage: install → build → slim runtime.
# Stateless; all runtime config (BFF_URL, AUTH_MODE, JWT keys, REALTIME_HUB_URL) is
# injected via env at deploy time (see deploy/CONFIG.md), never baked into the image.

FROM node:20-slim AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

FROM node:20-slim AS build
WORKDIR /app
RUN corepack enable
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined at build; override in CI when the realtime host is known.
ARG NEXT_PUBLIC_REALTIME_HUB_URL=""
ENV NEXT_PUBLIC_REALTIME_HUB_URL=${NEXT_PUBLIC_REALTIME_HUB_URL}
RUN pnpm build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN corepack enable
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.* ./
COPY --from=build /app/package.json ./package.json
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
# `next start` binds $PORT (3000); the script's -p 3100 is dev-only.
CMD ["node_modules/.bin/next", "start", "-p", "3000"]
