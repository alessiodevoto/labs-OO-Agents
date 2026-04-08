# Refactor Tracing: Composable Exporters + OTLP-First Default

**Status: IMPLEMENTED** (2026-03-09)

## Problem

### 1. Duplicate exporter code paths

`enable_tracing()` and `enable_tracing_langfuse()` share ~50% duplicate logic (TracerProvider setup, instrumentor wiring, LiteLLM patching, metadata capture). The eval pipeline must explicitly orchestrate both. Adding a new collector (Phoenix, Jaeger, Grafana Tempo) would mean yet another `enable_tracing_X()` function.

### 2. `session.id` is tied to JSONL file paths

`session.id` is currently an OTel **Resource attribute** (immutable, per-process). The `JSONLSpanExporter` overrides it at export time via `_session_id_from_filename()`. This means:

- OTLP exporters receive the wrong `session.id` in multi-session scenarios (eval pipeline, concurrent tasks).
- `set_trace_file()` only works for the JSONL exporter -- other exporters are unaware of session changes.
- Session identity is entangled with storage details (file paths).

The OTel-proper approach is `session.id` as a **span attribute** (per-span, set via ContextVar), which every exporter sees correctly. This is the same pattern [Langfuse uses](https://langfuse.com/docs/observability/features/sessions) with `propagate_attributes(session_id=...)`.

### 3. OTLP-first developer workflow (new)

The new `nemo-oo-agents-viewer` provides a local OTLP ingest endpoint, making the default developer workflow:

1. `nemo_oo_agents start-dev` (spins up viewer + OTLP endpoint on localhost:5001)
2. `enable_tracing()` sends to local OTLP endpoint by default
3. Developers see traces in the viewer immediately

JSONL file export remains available as an explicit choice but is no longer the default.

## Design Principles

1. **OTLP is the default** -- zero-config `enable_tracing()` sends to the local viewer
2. **JSONL uses the same wiring as every other exporter** -- no special code path
3. **The library does not know about specific collectors** -- it wires `SpanExporter` instances, nothing more
4. **One entry point** -- a single `enable_tracing()` that accepts exporters
5. **Composable** -- users pass 1..N exporters; the library picks `SimpleSpanProcessor` (local I/O) or `BatchSpanProcessor` (network) per-exporter
6. **Convenience helpers are fine** -- but they return `SpanExporter` instances, not configure the whole provider
7. **Sessions are exporter-agnostic** -- `set_session()` works for every exporter, not just JSONL

## Final API

### Exporter configuration

```python
from openinference_instrumentation_nemo_oo_agents import enable_tracing, exporters

# Zero-config default (OTLP to local viewer at localhost:5001)
enable_tracing()

# JSONL to a specific directory (no viewer needed)
enable_tracing(exporters=[exporters.jsonl("./traces")])

# Local viewer explicitly
enable_tracing(exporters=[exporters.local_otlp()])

# OTLP to any external collector (Jaeger, Tempo, Phoenix, Langfuse...)
enable_tracing(exporters=[exporters.otlp("http://localhost:4318/v1/traces")])

# Both local viewer + Langfuse
enable_tracing(exporters=[
    exporters.local_otlp(),
    exporters.langfuse(),
])

# Experiment/eval attributes
enable_tracing(
    experiment="capability-test-20260309",
    extra_resource_attrs={"eval.model": "gpt-4o", "eval.test_id": "test_001"},
)

# Bring your own exporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
enable_tracing(exporters=[OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces")])
```

`enable_tracing()` returns `None`. The `exporters` parameter defaults to `[exporters.local_otlp()]`. No deprecated parameters — pre-alpha, clean API only.

### Session management

```python
from openinference_instrumentation_nemo_oo_agents import set_session, get_session

# Set session for the current async context (uses ContextVar)
set_session("eval-run-042-sample-007")

# Read current session
current = get_session()  # -> "eval-run-042-sample-007" or None

# Eval pipeline example: each sample gets its own session
for sample in samples:
    set_session(f"eval-{run_id}-{sample.id}")
    await agent.run(sample.input)
```

`set_session()` replaces `set_trace_file()`. Every exporter sees the correct `session.id` on every span:
- **JSONL exporter**: routes spans to `{trace_dir}/{session_id}.jsonl`
- **OTLP local viewer**: overrides `session.id` on resource attributes (viewer groups by it)
- **OTLP exporters**: `session.id` is already a span attribute -- collectors group correctly
- **Console exporter**: prints `session.id` in the span output

### Lifecycle management

```python
from openinference_instrumentation_nemo_oo_agents import flush_traces, shutdown_traces

# Force-flush all pending spans (delegates to TracerProvider.force_flush)
flush_traces()

# Graceful shutdown: flush + release resources (delegates to TracerProvider.shutdown)
shutdown_traces()
```

These replace the pattern of `get_current_exporter().force_flush()` / `get_current_exporter().close_file()`.

## How `session.id` Works

```text
enable_tracing()
  → SessionSpanProcessor added to pipeline (before export processors)
  → Reads session_id ContextVar at span-start, sets span attribute "session.id"

set_session(session_id)
  → ContextVar sets session_id (not a file path)
  → SessionSpanProcessor stamps it on every span (span attributes)
  → OtlpJsonHttpExporter also reads ContextVar to override resource-level session.id
  → JSONL exporter reads span attribute to route files
  → Langfuse groups by session.id automatically
```

The unified ContextVar (`_current_session` in `_session.py`) is the single source of truth. The `OtlpJsonHttpExporter` reads it directly for resource-level override. The `SessionSpanProcessor` reads it for span-level stamping.

## File-by-File Changes (Implemented)

### 1. New file: `exporters.py`

Public module with factory functions that return `SpanExporter` instances. Each factory is a pure function: no global state, no TracerProvider setup.

- `local_otlp(endpoint?)` → `OtlpJsonHttpExporter` (zero-dep, for local viewer)
- `jsonl(trace_dir?, service_name?)` → `JSONLSpanExporter`
- `otlp(endpoint, headers?)` → `OTLPSpanExporter` (HTTP)
- `langfuse(host?, public_key?, secret_key?)` → `OTLPSpanExporter` (pre-configured for Langfuse)
- `console()` → `ConsoleSpanExporter`
- `_auto_detect_trace_dir()` → port of existing auto-detection logic

### 2. New file: `_session.py`

ContextVar-based session routing that all exporters use.

- `_current_session: ContextVar[str | None]`
- `set_session(session_id)` / `get_session()`

### 3. New file: `_session_processor.py`

Lightweight `SpanProcessor` that reads the session ContextVar at `on_start` and stamps `session.id` on every span. Added to TracerProvider **before** export processors.

### 4. Modified: `_otlp_http_exporter.py`

**Adopted from upstream.** Zero-dependency OTLP JSON/HTTP exporter using `urllib`.

**Key change:** Removed its own `_current_session_id` ContextVar and `set_session_id()`/`get_session_id()` functions. Now imports `_current_session` from `_session.py` — unified session management.

### 5. Refactored `__init__.py`

**Signature:**

```python
def enable_tracing(
    exporters: list[SpanExporter] | None = None,
    *,
    experiment: str | None = None,
    extra_resource_attrs: dict[str, Any] | None = None,
) -> None:
```

**Logic:**

1. Idempotency guard via `_enabled` flag.
2. Default to `[exporters.local_otlp()]` if no exporters specified.
3. Collect metadata via `get_all_metadata()`. `session.id` is **not** set on Resource.
4. Set `experiment` resource attribute (from param or `TRACE_EXPERIMENT` env var; omitted when unset).
5. Merge `extra_resource_attrs` into resource attributes.
6. Create `TracerProvider` with `Resource(attributes=metadata)`.
7. Add `SessionSpanProcessor` first.
8. For each exporter, pick processor: `SimpleSpanProcessor` for local (JSONL, Console, OtlpJsonHttp), `BatchSpanProcessor` for network (OTLPSpanExporter).
9. Generate default session ID via `set_session()` — format is `YYYYMMDD_HHMMSS_{uuid4hex8}` in UTC.
   This is a fallback so that spans always have a `session.id`; callers should use
   `set_session(meaningful_id)` for real workloads. The UUID suffix prevents collisions
   in multi-process scenarios (parallel test workers, concurrent evaluation processes).
10. Store `TracerProvider` in `_provider` for `flush_traces()` / `shutdown_traces()`.
11. Instrument nemo_oo_agents + LiteLLM once.
12. Print trace target (endpoint URL or file path).

**New top-level functions:** `flush_traces()`, `shutdown_traces()`

**Public exports:**

```python
__all__ = [
    "Agent006Instrumentor",
    "JSONLSpanExporter",
    "OtlpJsonHttpExporter",
    "enable_tracing",
    "exporters",
    "set_session",
    "get_session",
    "flush_traces",
    "shutdown_traces",
]
```

No deprecated shims — all old APIs removed, all callers migrated.

### 6. Updated `JSONLSpanExporter`

Reads `session.id` from **span attributes** for file routing:

```python
def _target_file(self, span):
    session_id = (span.attributes or {}).get("session.id")
    if session_id:
        return self.trace_dir / f"{session_id}.006trace.jsonl"
    return self.trace_file  # fallback to default
```

### 7. Updated trace viewer (`providers.py`)

Session-extraction checks **span attributes first**, then falls back to **resource attributes** for backward compatibility with old trace files.

### 8. Updated eval pipeline

**`evaluator.py`:** Both `run()` and `run_samples()` choose exporters based on `OTLP_ENDPOINT`:

```python
from openinference_instrumentation_nemo_oo_agents import enable_tracing, exporters

exporter_list = []
if use_otlp:
    exporter_list.append(exporters.local_otlp())
else:
    exporter_list.append(exporters.jsonl(traces_dir))
if langfuse_host:
    exporter_list.append(exporters.langfuse(host=langfuse_host))
enable_tracing(exporters=exporter_list, experiment=run_experiment_name)
```

**`pipeline.py`:** Uses `set_session()` for all exporters (unified), `flush_traces()` instead of `get_current_exporter().force_flush()`. OTLP-specific trace fetching via `otlp_io` preserved.

**`scoring.py`:** Judge trace routing uses session IDs (`{agent_session}_judge`, `{agent_session}_method_judge`) — exporter-agnostic.

### 9. Updated `pyproject.toml`

```toml
[project.optional-dependencies]
otlp = ["opentelemetry-exporter-otlp-proto-http>=1.20.0"]
```

### 10. Updated examples

- `examples/quickstart/06_tracing.py` -- `enable_tracing()` now sends to local viewer
- `examples/advanced/tracing_otlp.py` -- uses `enable_tracing(exporters=[...])` for external collectors
- `examples/util/presentation_utils.py` -- uses `set_session()` instead of `set_trace_file()`

### 11. Updated tests

- `conftest.py` -- resets `_enabled`, `_provider`, and `_current_session` ContextVar
- `test_idempotent_tracing.py` -- updated for new API (no return value, exporters param)
- `test_tracing_integration.py` -- uses SessionSpanProcessor, verifies session.id in span attributes
- `test_exporter_isolation.py` -- now tests session isolation via ContextVar (renamed to `TestSessionIsolation`)
- **New:** `test_exporters.py` -- factory tests for all exporter types
- **New:** `test_session_processor.py` -- SessionSpanProcessor unit tests

## Removed APIs (all callers migrated)

| Removed | Replacement |
|---|---|
| `enable_tracing(trace_dir=...)` | `enable_tracing(exporters=[exporters.jsonl(dir)])` |
| `enable_tracing_otlp()` | `enable_tracing(exporters=[exporters.otlp()])` |
| `enable_tracing_langfuse()` | `enable_tracing(exporters=[exporters.langfuse()])` |
| `get_current_exporter()` | `flush_traces()` / `shutdown_traces()` |
| `set_trace_file(path)` | `set_session(session_id)` |
| `get_trace_file()` | `get_session()` |
| `set_session_id()` (from `_otlp_http_exporter`) | `set_session(session_id)` (unified) |
| `get_session_id()` (from `_otlp_http_exporter`) | `get_session()` (unified) |
| `exporter.switch_file(path)` | `set_session(session_id)` |
| `exporter.close_file(path)` | `flush_traces()` |

## What Does NOT Change

- `Agent006Instrumentor` -- unchanged.
- `_hooks_impl.py` (span creation) -- unchanged.
- `_metadata.py` -- unchanged.
- `_litellm_patch.py` -- unchanged.
- Core framework (`src/nemo_oo_agents/`) -- zero changes needed.
- `nemo-oo-agents-viewer` package -- adopted as-is from upstream.
- `start-dev` CLI command -- adopted as-is from upstream.

## Test Results

```text
51 passed, 1 skipped (git metadata test)
```

All tests pass including:
- Exporter factory tests (JSONL, OTLP, Langfuse, Console)
- SessionSpanProcessor unit tests (ContextVar → span attribute, file routing)
- Session isolation tests (concurrent async contexts)
- Idempotency tests (multiple enable_tracing calls)
- Integration tests (metadata + session in span attributes)
- Flush/shutdown lifecycle tests
