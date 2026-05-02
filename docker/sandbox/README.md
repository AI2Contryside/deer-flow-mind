# AIO Sandbox Overlay Image

Thin overlay on top of `agent-infra/all-in-one-sandbox`. Adds:

- byted internal proxy on every network RUN (Dockerfile mirrors `backend/Dockerfile`)
- agent-friendly system tools: `jq git unzip ffmpeg poppler-utils libreoffice-{writer,calc,impress}`
- CJK + emoji + DejaVu fonts (chart / ppt / image skills render Chinese instead of tofu)
- Python file-processing stack baked in (Excel / Word / PDF / PPT / images / dataframe / charts)

What it does **not** add: docling, playwright, ML wheels, secrets. See the long-form rationale in [`Dockerfile`](./Dockerfile) header.

## Layout

```
deer-flow-mind/docker/sandbox/
├── Dockerfile          # overlay on ${BASE} (default: vefaas all-in-one-sandbox:latest)
├── requirements.lock   # Python file-processing stack
└── README.md           # this file
```

## Build

```bash
# From repo root. Build context is this directory because requirements.lock
# is the only file we COPY in.
cd deer-flow-mind/docker/sandbox

TAG=$(git -C ../../.. rev-parse --short HEAD)
docker build \
  --build-arg BASE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest \
  -t trademind/aio-sandbox:${TAG} \
  -t trademind/aio-sandbox:latest \
  .
```

Build-arg overrides:

| Arg            | Default                                                                                       | When to override                              |
| -------------- | --------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `BASE`         | `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest`          | Pin a specific upstream digest for reproducibility, or swap to a mirror if the volcengine source is unreachable on your network. |
| `HTTP_PROXY`   | `http://sys-proxy-rd-relay.byted.org:8118`                                                    | Outside the byted network, set both `HTTP_PROXY` and `HTTPS_PROXY` to `""` (the build will then go direct). |
| `HTTPS_PROXY`  | `http://sys-proxy-rd-relay.byted.org:8118`                                                    | Same as above. |
| `NO_PROXY`     | `.byted.org,localhost,127.0.0.0/8,10.0.0.0/8`                                                 | Add corp-internal hostnames. |

## Distribution to `devbox_agent`

Pick **one** of the two paths depending on whether a private registry is available.

### Path A — push/pull via private registry (preferred)

```bash
# Local: tag and push
docker tag  trademind/aio-sandbox:${TAG}    harbor.byted.org/trademind/aio-sandbox:${TAG}
docker push harbor.byted.org/trademind/aio-sandbox:${TAG}

# On devbox: pull
ssh devbox_agent 'docker pull harbor.byted.org/trademind/aio-sandbox:'"${TAG}"
```

Then in `config.yaml`:

```yaml
sandbox:
  use: src.community.aio_sandbox:AioSandboxProvider
  image: harbor.byted.org/trademind/aio-sandbox:abc1234   # pin sha; do NOT use :latest in prod config
```

### Path B — `docker save` + `scp` + `docker load`

Use this if no internal registry is reachable. Slower (full image bytes, no
layer dedup) but needs no infrastructure.

```bash
# Local
docker save trademind/aio-sandbox:${TAG} | gzip > /tmp/aio-sandbox-${TAG}.tar.gz
scp /tmp/aio-sandbox-${TAG}.tar.gz devbox_agent:/tmp/

# Devbox
ssh devbox_agent 'docker load < /tmp/aio-sandbox-'"${TAG}"'.tar.gz && rm /tmp/aio-sandbox-'"${TAG}"'.tar.gz'
```

`config.yaml` references the local tag:

```yaml
sandbox:
  use: src.community.aio_sandbox:AioSandboxProvider
  image: trademind/aio-sandbox:abc1234
```

## Verify on devbox

After the image lands and `config.yaml` is switched, force-rebuild deer-flow:

```bash
./scripts/deploy/deploy-deerflow.sh --force-rebuild
```

Then trigger a `bash` tool call from any thread and confirm:

```bash
ssh devbox_agent 'docker ps | grep deer-flow-sandbox-'                       # container is up
ssh devbox_agent 'docker exec $(docker ps -q -f name=deer-flow-sandbox-) \
                  python3 -c "import openpyxl, docx, pypdf, pptx; print(\"ok\")"'
```

`AioSandboxProvider`'s warm-pool means you should only see one container per
active thread. After 10 minutes idle (default `idle_timeout`), the container
disappears.

## Maintenance

- **Bumping deps:** edit `requirements.lock`, rebuild, push, pin new tag in
  `config.yaml`. Do not amend an already-deployed tag — old runtimes may
  still reference it.
- **Image size budget:** target ≤ 4 GB compressed. If larger, the first
  candidate to drop is `libreoffice-*` (~400 MB) — it's a convenience for
  agent-driven format conversion, not a hard requirement.
- **Polars on old CPUs:** if the build succeeds but `import polars` segfaults
  with "illegal instruction" at runtime, swap `polars` for `polars-lts-cpu`
  in `requirements.lock` (the line is already commented in place) and rebuild.
- **Cleaning up dangling sandbox containers on devbox** (after image bumps):

  ```bash
  ssh devbox_agent 'docker ps -a --filter name=deer-flow-sandbox- -q | xargs -r docker rm -f'
  ```

## Why these specific deps

See the table at the top of `requirements.lock`. Short version: every package
is there to either (a) replace a runtime `pip install` we observed in
`skills/public/data-analysis/scripts/analyze.py` (cold-start tax), or (b)
fill a "user uploaded an Excel/Word/PDF, agent should be able to read it
without any setup" gap that's currently a soft failure mode.

`docling` is **not** baked in: it lives in the gateway container, runs once
at upload time, and emits `<file>.docling.json` sidecars — see
`backend/CLAUDE.md` § File Upload. Sandbox agents read the sidecars, not the
originals, so they don't need docling itself.
