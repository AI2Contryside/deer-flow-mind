# ── Frontend Dockerfile for DeerFlow (internal build) ──────────────────────────

# ARG for pnpm store path
ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store

# ── Base image with Node 22
FROM node:22-slim AS base
ARG PNPM_STORE_PATH

# Enable corepack and install pnpm
RUN corepack enable && corepack install -g pnpm@10.26.2
RUN pnpm config set store-dir ${PNPM_STORE_PATH}

WORKDIR /app
COPY frontend ./frontend

# Disable Next.js Google Fonts fetch during build
ENV NEXT_DISABLE_GOOGLE_FONTS=1
ENV NEXT_TELEMETRY_DISABLED=1

# ── Dev target: install deps only
FROM base AS dev
RUN cd /app/frontend && pnpm install --frozen-lockfile
EXPOSE 3000

# ── Builder target: install deps + build production
FROM base AS builder
RUN cd /app/frontend && pnpm install --frozen-lockfile
# Skip env validation + build
RUN cd /app/frontend && SKIP_ENV_VALIDATION=1 pnpm build

# ── Prod target: minimal runtime with prebuilt frontend
FROM node:22-slim AS prod
ARG PNPM_STORE_PATH

RUN corepack enable && corepack install -g pnpm@10.26.2
RUN pnpm config set store-dir ${PNPM_STORE_PATH}

WORKDIR /app
COPY --from=builder /app/frontend ./frontend

EXPOSE 3000
CMD ["sh", "-c", "cd /app/frontend && pnpm start"]