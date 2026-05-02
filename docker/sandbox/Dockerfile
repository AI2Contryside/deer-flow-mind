# syntax=docker/dockerfile:1.7
#
# AIO Sandbox overlay image
# =========================
# Thin overlay on top of agent-infra/all-in-one-sandbox. We only ADD:
#   - byted-internal-network-friendly proxy on every network RUN
#   - system tools the agent reaches for via `bash` (jq / git / unzip / ffmpeg / …)
#   - CJK + emoji fonts so chart-visualization / ppt-generation render Chinese
#   - Python file-processing stack (Excel / Word / PDF / PPT / CSV) baked in,
#     so data-analysis et al. don't `pip install` on every cold start.
#
# We INTENTIONALLY DO NOT:
#   - override ENTRYPOINT / CMD — the upstream image runs the agent_sandbox
#     HTTP server on port 8080; LocalContainerBackend / RemoteSandboxBackend
#     both depend on that contract.
#   - bake credentials. Per-call ERPNEXT_* / OSS / etc. flow in via
#     `tools._extract_erpnext_env` and the AioSandbox shell prefix; they
#     must never live in this image.
#   - install docling / playwright / large ML wheels. Those run in the
#     backend gateway container, not the sandbox.
#
# Build:
#   docker build \
#     --build-arg BASE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest \
#     -t trademind/aio-sandbox:$(git rev-parse --short HEAD) \
#     -t trademind/aio-sandbox:latest \
#     -f deer-flow-mind/docker/sandbox/Dockerfile \
#     deer-flow-mind/docker/sandbox/
#
# After build, point sandbox.image in config.yaml at the resulting tag.
# See ./README.md for distribution to devbox_agent (push vs. save/load).

ARG BASE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
FROM ${BASE}

# ── byted-internal proxy (mirrors backend/Dockerfile pattern) ────────────────
# These are ARG (build-time) not ENV (runtime) so the resulting image stays
# proxy-clean: a sandbox container should never auto-route traffic through
# the byted relay at runtime — agent code that needs HTTP must opt in.
ARG HTTP_PROXY=http://sys-proxy-rd-relay.byted.org:8118
ARG HTTPS_PROXY=http://sys-proxy-rd-relay.byted.org:8118
ARG NO_PROXY=.byted.org,localhost,127.0.0.0/8,10.0.0.0/8

# ── 1) System packages ───────────────────────────────────────────────────────
# Single RUN to keep layers minimal. apt cleanup at end of the layer is
# important: Debian's lists weigh ~50MB and we don't need them at runtime.
RUN set -eux \
 && export http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl wget \
      git \
      jq \
      unzip zip \
      ffmpeg \
      poppler-utils \
      libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
        --no-install-recommends \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*
# Notes on system packages:
#   - poppler-utils gives `pdftotext` / `pdfimages` — agent often shell-pipes
#     these instead of writing pypdf code for one-off extractions.
#   - libreoffice (~400MB) enables headless docx/xlsx/pptx → pdf conversion
#     via `soffice --headless --convert-to pdf …`. If the image is too big,
#     drop the `libreoffice-*` lines — backend already runs docling for the
#     structured-extraction path; libreoffice is purely for agent ergonomics.

# ── 2) Fonts (CJK + emoji) ───────────────────────────────────────────────────
# Separate layer because fonts rarely change and skills (chart, ppt, image)
# need them; without CJK, matplotlib/pillow render Chinese as tofu.
RUN set -eux \
 && export http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      fonts-noto-cjk fonts-noto-cjk-extra \
      fonts-noto-color-emoji \
      fonts-dejavu-core \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -fv >/dev/null

# ── 3) Python file-processing stack ──────────────────────────────────────────
# requirements.lock is COPYed first so this layer rebuilds only when the
# lockfile changes — apt and font layers above stay cached.
COPY requirements.lock /tmp/requirements.lock

# Use the Python that ships with the upstream sandbox image (which `python3`
# resolves to). If upstream is on 3.11 instead of 3.12, the lock should still
# resolve — the bounds we picked are deliberately Python-version-agnostic.
RUN set -eux \
 && export http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
 && python3 -m pip install --no-cache-dir --upgrade pip \
 && python3 -m pip install --no-cache-dir -r /tmp/requirements.lock \
 && python3 -c "import openpyxl, docx, pypdf, pdfplumber, fitz, pptx, PIL, pandas, polars, duckdb, lxml, bs4; print('python deps OK')" \
 && rm -f /tmp/requirements.lock
# That final smoke import catches the most common wheel-platform mismatches
# (e.g. pymupdf has fragile manylinux wheels) at build time, not at first
# agent invocation.

# ── 4) Node sanity check (chart-visualization needs node>=18) ────────────────
# We don't `npm install` anything here — chart-visualization/scripts/generate.js
# only uses the `fs` builtin (it serializes data and POSTs to an external chart
# service). If a future skill adds real npm deps, give it its own
# `package.json` under skills/<name>/ and let it `npm install` per-skill;
# don't grow this base image unbounded.
RUN set -eux \
 && if command -v node >/dev/null 2>&1; then \
      echo "node $(node --version) present"; \
      node --version | awk -F. '{ ver=$1; sub(/^v/, "", ver); if (ver+0 < 18) { print "FATAL: node <18 in base image"; exit 1 } }'; \
    else \
      echo "WARN: node missing in base image — chart-visualization skill will fail until upstream adds it or we install it here"; \
    fi

# ── 5) Locale (UTF-8 default) ────────────────────────────────────────────────
# pandas / openpyxl / pillow with CJK content all assume UTF-8; some upstream
# sandbox base images ship with C locale which trips Chinese filenames.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONIOENCODING=UTF-8

# Image labels — make `docker inspect` self-documenting and let our deploy
# scripts assert they're loading the right image.
LABEL org.opencontainers.image.title="trademind-aio-sandbox" \
      org.opencontainers.image.description="AIO sandbox overlay with file-processing stack baked in" \
      org.opencontainers.image.source="https://github.com/AI2Contryside/deer-flow-mind" \
      trademind.sandbox.role="agent-execution"

# ENTRYPOINT / CMD intentionally inherited from base.
