# Backend Development Dockerfile
FROM python:3.12-slim

ARG NODE_MAJOR=22

# Install system dependencies + Node.js (provides npx for MCP servers)
RUN export http_proxy=http://sys-proxy-rd-relay.byted.org:8118 https_proxy=http://sys-proxy-rd-relay.byted.org:8118 no_proxy=.byted.org \
    && apt-get update && apt-get install -y \
    curl \
    build-essential \
    gnupg \
    ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

# Install uv from a pinned versioned image (avoids curl|sh from untrusted remote)
COPY --from=ghcr.io/astral-sh/uv:0.7.20 /uv /uvx /usr/local/bin/

# Set working directory
WORKDIR /app

# Copy backend source code
COPY backend ./backend

# Bake the prod config skeleton into the image so APP_ENV=prod works without
# any bind-mount. Only the placeholder-only file is baked — config.dev.yaml
# is intentionally NOT copied so plaintext dev API keys never end up in a
# prod image. Dev workflow continues to bind-mount config.yaml via the
# existing docker-compose.yaml, which keeps `${DEER_FLOW_CONFIG_PATH}`
# pointed at the host's working file.
#
# AppConfig.resolve_config_path looks for `config.<env>.yaml` in cwd then
# cwd's parent. The langgraph / gateway commands cd into /app/backend, so
# /app is the parent — landing the file there lets the loader find it
# without any further wiring.
COPY config.prod.yaml ./
# Bake an empty extensions config so `from_file()` returns the empty
# default rather than walking off looking for a non-existent file. Real
# MCP / skills config can still be injected at runtime via
# DEER_FLOW_EXTENSIONS_CONFIG_PATH.
COPY extensions_config.json ./

# Install dependencies with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    sh -c "export http_proxy=http://sys-proxy-rd-relay.byted.org:8118; export https_proxy=http://sys-proxy-rd-relay.byted.org:8118; export no_proxy=.byted.org; cd backend && uv sync"

# Expose ports (gateway: 8001, langgraph: 2024)
EXPOSE 8001 2024

# Default command (can be overridden in docker-compose)
CMD ["sh", "-c", "uv run uvicorn src.gateway.app:app --host 0.0.0.0 --port 8001"]
