# Hybrid Trace Provider Design

## Goal

Create a `HybridProvider` that queries both Langfuse and local files simultaneously, presenting a unified view of all traces.

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                      HybridProvider                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐          ┌─────────────────┐               │
│  │  LocalProvider  │          │ LangfuseProvider│               │
│  │                 │          │                 │               │
│  │ *.trace.jsonl   │          │  Langfuse API   │               │
│  │ local files     │          │  remote traces  │               │
│  └─────────────────┘          └─────────────────┘               │
│           │                            │                         │
│           └──────────┬─────────────────┘                         │
│                      ▼                                           │
│              ┌──────────────┐                                    │
│              │    Merge     │                                    │
│              │   Results    │                                    │
│              └──────────────┘                                    │
│                      │                                           │
│                      ▼                                           │
│              Unified API Response                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Design Decisions

### 1. Source Identification

Each `TraceGroup` gets a `source` field to identify origin:

```python
class TraceGroup(BaseModel):
    id: str                    # session_id
    name: str
    modified: datetime | None
    size: int | None
    event_count: int | None
    source: str | None = None  # NEW: "local" | "langfuse"
```

The `id` field becomes `{source}::{session_id}` to ensure uniqueness:
- `local::abc123-def456`
- `langfuse::abc123-def456`

This handles the (rare) case where same session_id exists in both sources.

### 2. Provider Routing

HybridProvider maintains a mapping of `prefixed_id → (source, original_id)`:

```python
class HybridProvider(TraceProvider):
    def __init__(self, local: LocalProvider, langfuse: LangfuseProvider | None):
        self.local = local
        self.langfuse = langfuse  # Optional - can run without Langfuse
        self._session_routing: dict[str, tuple[str, str]] = {}
```

When `get_session("local::abc123")` is called:
1. Parse prefix → `source="local"`, `original_id="abc123"`
2. Route to `self.local.get_session("abc123")`

### 3. Graceful Degradation

If Langfuse is not configured or unavailable:
- `list_groups()` returns only local traces
- No errors thrown
- UI shows warning indicator

```python
async def list_groups(self) -> list[TraceGroup]:
    groups = []

    # Always get local traces
    local_groups = await self.local.list_groups()
    for g in local_groups:
        g.source = "local"
        g.id = f"local::{g.id}"
        groups.append(g)

    # Get Langfuse traces if available
    if self.langfuse:
        try:
            lf_groups = await self.langfuse.list_groups()
            for g in lf_groups:
                g.source = "langfuse"
                g.id = f"langfuse::{g.id}"
                groups.append(g)
        except Exception as e:
            logger.warning(f"Langfuse unavailable: {e}")

    return groups
```

### 4. Configuration

Update `ConfigFile` to support hybrid mode:

```json
{
  "provider": "hybrid",
  "trace_directories": [
    "agents/tpm-agent/traces/",
    "experiments/capability_eval/traces/"
  ],
  "langfuse": {
    "host": "http://localhost:3000",
    "public_key": "pk-...",
    "secret_key": "sk-..."
  }
}
```

When `provider: "hybrid"`:
- LocalProvider is always initialized with `trace_directories`
- LangfuseProvider is initialized only if `langfuse` config is present

### 5. Annotation Routing

Annotations are stored in the source system:

```python
async def create_annotation(self, annotation: Annotation) -> Annotation:
    source, original_id = self._parse_session_id(annotation.session_id)
    annotation.session_id = original_id  # Use original ID for storage

    if source == "local":
        return await self.local.create_annotation(annotation)
    elif source == "langfuse":
        return await self.langfuse.create_annotation(annotation)
```

### 6. Search & Filtering

Future: Add cross-source search capability:

```python
async def search(self, query: str) -> list[TraceGroup]:
    """Search across all sources."""
    results = []

    # Search local (fast - grep on files)
    results.extend(await self.local.search(query))

    # Search Langfuse (API-based)
    if self.langfuse:
        results.extend(await self.langfuse.search(query))

    return results
```

## Implementation Status

### Phase 1: Core HybridProvider ✅

1. ✅ Add `source` field to `TraceGroup` model - [models.py](../util/trace-viewer/backend/models.py)
2. ✅ Create `HybridProvider` class - [providers.py:1872](../util/trace-viewer/backend/providers.py#L1872)
3. ✅ Implement `list_groups()` with source prefixing
4. ✅ Implement `get_session()` with routing
5. ✅ Implement annotation CRUD with routing
6. ✅ Update `get_provider()` factory to handle `"hybrid"` - [main.py:1032](../util/trace-viewer/backend/main.py#L1032)
7. ✅ Add `/api/provider/status` endpoint - [main.py:669](../util/trace-viewer/backend/main.py#L669)

### Phase 2: Frontend Updates ✅

1. ✅ Add source badge to trace list (🖥️ local, ☁️ langfuse) - [trace-list.html](../util/trace-viewer/frontend/trace-list.html)
2. ✅ Add source filter dropdown
3. ✅ Handle prefixed session IDs in URLs (transparent - existing code works)
4. ✅ Add CSS for source badges - [main.css](../util/trace-viewer/frontend/css/main.css)

### Phase 3: Error Handling & Polish (Future)

1. 🔲 Add Langfuse health check on startup
2. ✅ Show connection status in UI (provider-status span)
3. 🔲 Add retry logic for Langfuse failures
4. 🔲 Cache Langfuse results with TTL

## API Changes

### Response Format

```json
{
  "groups": [
    {
      "id": "local::trace_20250112_143022",
      "name": "trace_20250112_143022",
      "source": "local",
      "modified": "2025-01-12T14:30:22Z",
      "event_count": 42
    },
    {
      "id": "langfuse::abc123-def456",
      "name": "Customer Service Session",
      "source": "langfuse",
      "modified": "2025-01-12T10:15:00Z",
      "event_count": 128
    }
  ]
}
```

### URL Format

Session URLs use the prefixed ID:
```text
/trace?session_id=local::trace_20250112_143022
/trace?session_id=langfuse::abc123-def456
```

## Usage

### Enable Hybrid Mode

Set `provider: "hybrid"` in `trace_viewer_config.json`:

```json
{
  "provider": "hybrid",
  "trace_directories": [
    "agents/tpm-agent/traces/",
    "experiments/capability_eval/traces/"
  ],
  "langfuse": {
    "host": "http://localhost:3000",
    "public_key": "pk-...",
    "secret_key": "sk-..."
  }
}
```

Or use environment variable:
```bash
export TRACE_VIEWER_PROVIDER=hybrid
```

### What You'll See

- **Trace list**: Each trace shows a source badge (🖥️ local, ☁️ langfuse)
- **Filter dropdown**: Filter by source when multiple sources are active
- **Status indicator**: Shows active sources in toolbar
- **Seamless routing**: Clicking a trace loads from the correct source automatically

### Graceful Degradation

If Langfuse is not configured or unavailable:
- Local traces still work
- Source filter shows "Langfuse (unavailable)"
- No errors thrown

## Open Questions

1. **Caching strategy**: Should HybridProvider cache merged results, or let each sub-provider cache independently?
   - **Decision**: Let sub-providers cache independently. Simpler and each can optimize for its source.

2. **Annotation sync**: Should annotations made locally be synced to Langfuse?
   - **Decision**: No. Keep storage separate. Upload workflow handles explicit sync.

3. **Real-time updates**: Should Langfuse poll for new traces?
   - **Decision**: No polling. Manual refresh via UI. File watcher only for local.
