# Fix Trace Viewer Deployment After OTEL Migration

## Problem

After migrating from a custom file-based tracing approach to standard OTEL (OpenTelemetry Protocol / OTLP),
the trace viewer Docker image built by CI (`util/trace-viewer/Dockerfile`) is stale and still uses:
- The old `util/trace-viewer/backend/main.py` (file-based, reads TRACE_DIRECTORIES)
- `--group trace-viewer` dep group (old approach)
- CMD: `python -m uvicorn backend.main:app ...`

The new viewer is the `agent006-viewer` package at `packages/agent006-viewer/`, which:
- Accepts traces via OTLP HTTP at `/v1/traces`
- Stores traces in SQLite (`TRACE_STORE_DB` env var, defaults to `traces.db` in cwd)
- Is started with `agent006 start-dev` (CLI) or `python -m agent006_viewer`
- Defaults to port 5001 (configurable via `TRACE_VIEWER_PORT` or `VIEWER_PORT` env var)
- Still exposes `/api/version` (health check endpoint unchanged)

**TPM agent tracing**: The `tpm-agent` runner is updated to use dual-export tracing (JSONL + OTLP).
JSONL files are retained because multiple downstream tools consume them: `trace_explorer`,
`build_sft_dataset.py`, `trace_converter.py`, `analyze_traces.py`, and the evaluation pipeline.
OTLP is added for live viewing in the trace-viewer's SQLite store.

## Changes

### 1. `util/trace-viewer/Dockerfile`

Rewrite to use `agent006-viewer` package instead of old trace-viewer code:

**Stage 1a** (PyPI deps only — no workspace packages):
```dockerfile
uv sync --frozen --no-default-groups --extra viewer --no-install-workspace
```
(was: `--group trace-viewer`)
- `--extra viewer` installs the `agent006-viewer` transitive PyPI deps only (fastapi, uvicorn, etc.)
- `--no-install-workspace` skips workspace packages (agent006-viewer itself, viewer_utils) — they're installed in Stage 1c

**Stage 1c** (workspace packages + root project):
```dockerfile
uv sync --locked --no-editable --no-default-groups --extra viewer
```

**Remove COPY statements** for old files:
- `util/trace-viewer/backend/` — installed as package
- `util/trace-viewer/frontend/` — bundled in `agent006-viewer` wheel
- `util/trace-viewer/trace_viewer_config.json` — not used
- `util/viewer_utils/` — installed as workspace package, no manual copy needed

**Keep**:
- `COPY --chown=app:app util/config/ /app/util/config/` — keep for playground models.yaml
- Add `ENV AGENT006_MODELS_CONFIG=/app/util/config/models.yaml` — `trace_routes.py` uses this env
  var to locate models.yaml; without it, it looks in `cwd/models.yaml`. Since WORKDIR changes
  from `/app/util/trace-viewer` to `/app`, this explicit path is needed.

**Remove** `WORKDIR /app/util/trace-viewer` — CMD no longer requires specific cwd

**Keep** `ENV AGENT006_PROJECT_ROOT=/app`:
- `viewer_utils` (runtime dependency of `agent006-viewer`) has `paths.py` which uses this env var
- Keep it set to `/app` as before

**Update env vars**:
- Remove `ENV TRACE_DIRECTORIES=/app/traces` — old file-based env var, not used by new viewer
- Keep `ENV TRACE_VIEWER_PORT=8001` — `__main__.py` reads this to bind on 8001
- Add `ENV TRACE_STORE_DB=/app/traces/traces.db` — SQLite DB persisted in the traces volume

**Update CMD**:
```dockerfile
CMD ["python", "-m", "agent006_viewer"]
```
(was: `["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]`)

**Keep**:
- Port 8001 (via `TRACE_VIEWER_PORT=8001` env var)
- Health check at `/api/version` (still present in new viewer's `trace_routes.py:136`)
- Non-root user `app`
- `EXPOSE 8001`

### 2. `deploy/docker-compose.yml`

**`trace-viewer` service**:
- Remove `TRACE_DIRECTORIES=/app/traces` — old file-based env var
- Add `TRACE_STORE_DB=/app/traces/traces.db` — SQLite DB in the persistent volume
- Change volume from `traces:/app/traces:ro` → `traces:/app/traces` — viewer needs write access for SQLite

**`tpm-agent` service**:
- Keep `TRACE_DIR=/app/traces` — JSONL files consumed by downstream tools (trace_explorer,
  build_sft_dataset, trace_converter, analyze_traces, evaluation pipeline)
- Add `OTLP_ENDPOINT=http://trace-viewer:8001/v1/traces` — runner sends spans via OTLP to viewer
- Add `depends_on` with `condition: service_started` for trace-viewer — starts viewer first but
  doesn't block tpm-agent if viewer healthcheck is slow

### 3. `agents/tpm-agent/runner.py`

Add OTLP exporter alongside existing JSONL:
- When `TRACE_DIR` is set: JSONL exporter writes trace files for downstream analysis tools
- When `OTLP_ENDPOINT` is set: OTLP exporter sends spans to viewer for live viewing
- Both exporters can be active simultaneously (dual export)

## Files Changed

1. `util/trace-viewer/Dockerfile` — rewrite for new viewer
2. `deploy/docker-compose.yml` — update env vars, volume mode, and service ordering
3. `agents/tpm-agent/runner.py` — use dual-export tracing (JSONL + OTLP)

## Testing

Build the Docker image locally:
```bash
docker build -f util/trace-viewer/Dockerfile -t trace-viewer-test .
docker run --rm -e TRACE_VIEWER_PORT=8001 -p 8001:8001 trace-viewer-test
curl http://localhost:8001/api/version

# Verify SQLite DB is created
docker exec trace-viewer-test ls -lh /app/traces/traces.db
```
