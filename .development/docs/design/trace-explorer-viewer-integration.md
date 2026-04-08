# Trace Explorer: Viewer API Integration & OTLP Migration

**Issue:** [#137](https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/137)
**Date:** 2026-03-24

## Goal

Allow agents (Claude Code, Cursor) to explore traces served by the viewer via the
existing `trace-explorer` CLI. Migrate trace_explorer to accept OTLP-format spans
natively (drop legacy flat-attribute format).

## Architecture

```
Viewer UI                      Agent (Claude/Cursor)
    |                                |
    | "Debug with Agent" button      |
    | copies prompt with span-id     |
    v                                v
                              trace-explorer CLI
                              --viewer URL --span-id X
                                     |
                          fetches spans from viewer API
                          GET /api/trace?session_id=...
                                     |
                              normalizes OTLP → internal
                                     |
                              TraceExplorer (unchanged)
                              progressive disclosure output
```

## Design Decisions

1. **Normalize at the boundary.** OTLP spans are converted to the internal flat-dict
   format once at load time (`_normalize_otlp_span()`). This avoids touching the ~74
   attribute access sites in `explorer.py`. The internal representation stays:
   ```python
   {"span_id": "...", "attributes": {"llm.model": "claude-opus", ...}, ...}
   ```

2. **No legacy format support.** `_load_spans()` assumes OTLP format. Existing
   legacy `.jsonl` files will stop working (they need re-export from viewer or
   re-generation with current instrumentation).

3. **CLI is the interface.** No MCP server. Both Claude Code and Cursor invoke
   `trace-explorer` via bash.

## Format Mapping: OTLP → Internal

| OTLP field | Internal field | Transform |
|---|---|---|
| `spanId` | `span_id` | rename |
| `traceId` | `trace_id` | rename |
| `parentSpanId` | `parent_span_id` | rename |
| `startTimeUnixNano` (str) | `start_time` (int) | `int()` |
| `endTimeUnixNano` (str) | `end_time` (int) | `int()` |
| (computed) | `duration_ns` | `end_time - start_time` |
| `attributes` (list[KeyValue]) | `attributes` (flat dict) | `_otlp_attrs_to_dict()` |
| `status.code` (int: 0/1/2) | `status.status_code` (str) | 0→"OK", 1→"OK", 2→"ERROR" (map UNSET→OK for compat) |
| `status.message` | `status.description` | rename |
| `_resource` | `resource` | rename, flatten resource attrs |
| `events` | `events` | pass through (OTLP event attrs NOT normalized — explorer doesn't access them today) |
| `name` | `name` | pass through |
| `kind` | `kind` | pass through (new field, unused by explorer) |

## Implementation Steps

### Step 1: Add `_normalize_otlp_span()` to `explorer.py`

New module-level function (~40 lines) that converts one OTLP span dict to the
internal format. Also add `_otlp_attrs_to_dict()` and `_extract_any_value()` (copy
from `otlp_store.py` to avoid cross-package dependency — needed for nested array
and kvlist attribute values).

```python
def _normalize_otlp_span(span: dict[str, Any]) -> dict[str, Any]:
    """Convert an OTLP span to internal flat-attribute format."""
    status = span.get("status", {})
    code = status.get("code", 0)
    # Map 0 (UNSET) to "OK" for compat — explorer checks `== "ERROR"`
    status_map = {0: "OK", 1: "OK", 2: "ERROR"}

    start_ns = int(span.get("startTimeUnixNano", 0))
    end_ns = int(span.get("endTimeUnixNano", 0))

    resource = span.get("_resource", {})
    if isinstance(resource.get("attributes"), list):
        resource = {"attributes": _otlp_attrs_to_dict(resource["attributes"])}

    return {
        "span_id": span.get("spanId", ""),
        "trace_id": span.get("traceId", ""),
        "parent_span_id": span.get("parentSpanId"),
        "name": span.get("name", ""),
        "kind": span.get("kind"),
        "start_time": start_ns,
        "end_time": end_ns,
        "duration_ns": end_ns - start_ns if end_ns and start_ns else 0,
        "attributes": _otlp_attrs_to_dict(span.get("attributes", [])),
        "events": span.get("events", []),
        "status": {
            "status_code": status_map.get(code, "UNSET"),
            "description": status.get("message"),
        },
        "resource": resource,
    }
```

### Step 2: Update `_load_spans()` for OTLP format

Change `_load_spans()` to normalize each span after JSON parsing. Only OTLP
format is accepted. Preserve existing error tolerance.

```python
def _load_spans(trace_path: str | Path) -> list[dict[str, Any]]:
    spans = []
    parse_errors = 0
    with open(trace_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    raw = json.loads(line)
                    spans.append(_normalize_otlp_span(raw))
                except json.JSONDecodeError as e:
                    parse_errors += 1
                    if not _quiet_mode and parse_errors <= 3:
                        print(f"Warning: Parse error at line {line_num}: {e}", file=sys.stderr)
    if not _quiet_mode and parse_errors > 3:
        print(f"Warning: {parse_errors} total JSON parse errors", file=sys.stderr)
    return spans
```

### Step 3: Add `from_viewer()` classmethod

New classmethod on `TraceExplorer` that fetches spans from the viewer API:

```python
@classmethod
def from_viewer(
    cls,
    base_url: str,
    session_id: str,
    eval_result: EvalContextData | None = None,
    root_generation_index: int | None = None,
) -> TraceExplorer:
    """Load a trace from the viewer API."""
    import urllib.request

    # Fetch all spans (paginated)
    all_spans = []
    offset = 0
    page_size = 500
    while True:
        url = f"{base_url}/api/trace?session_id={session_id}&limit={page_size}&offset={offset}"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
        raw_spans = data.get("events", [])
        all_spans.extend(_normalize_otlp_span(s) for s in raw_spans)
        if not data.get("has_more", False):
            break
        offset += page_size

    # Same pipeline as from_file
    sessions = _parse_trace_from_spans(all_spans)
    eval_result = eval_result or cls._extract_eval_from_spans(all_spans)
    return cls(
        sessions=sessions,
        trace_file=f"viewer://{session_id}",
        eval_result=eval_result,
        raw_spans=all_spans,
    )
```

Uses `urllib.request` (stdlib) to avoid adding a dependency on `requests`/`httpx`.

### Step 4: Add `find_span()` method for `--span-id` support

New method that locates a span by ID and returns contextual information (which
session, which turn, position) so the agent has breadcrumbs to navigate:

```python
def find_span(self, span_id: str) -> str:
    """Find a span by ID and show it with navigation breadcrumbs."""
    # Search sessions for the span
    for session in self._all_sessions:
        for i, turn in enumerate(session.turns):
            if hasattr(turn, 'span_id') and turn.span_id == span_id:
                header = f"# Span {span_id[:8]} → session {_short_id(session.session_id)}, turn {i} of {len(session.turns)}\n"
                return header + self.get_turn(session.session_id, i)
    # Fallback: check raw spans
    raw = self.get_raw_span(span_id)
    return f"# Span {span_id[:8]} (raw, not associated with a turn)\n{raw}"
```

### Step 5: Extend CLI

Update `main()` in `explorer.py` to add new arguments:

```
trace-explorer [trace_file]                              # file mode (existing)
trace-explorer --viewer URL --session-id ID [options]    # viewer mode (new)
trace-explorer --viewer URL --span-id ID [options]       # span jump (new)
```

New arguments:
- `--viewer URL` — viewer base URL (e.g., `http://localhost:5001`)
- `--session-id ID` — session to load from viewer
- `--span-id ID` — jump to specific span (breadcrumb output)

Logic:
- If `--viewer` given: use `TraceExplorer.from_viewer()`
- If `--span-id` given without `--session-id`: fetch session list from viewer, find
  which session contains the span (or require `--session-id`)
- If `trace_file` given: use `TraceExplorer.from_file()` (OTLP format)
- Error if neither `trace_file` nor `--viewer` given

### Step 6: Update `__init__.py` exports

Export the new `find_span` method (it's on the class, so automatic) and ensure
`from_viewer` is documented.

## Files Changed

| File | Change |
|---|---|
| `packages/trace_explorer/src/trace_explorer/explorer.py` | Add `_extract_any_value()`, `_otlp_attrs_to_dict()`, `_normalize_otlp_span()`, `from_viewer()`, `find_span()`. Update `_load_spans()`, `main()`. |
| `packages/trace_explorer/pyproject.toml` | No changes expected (no new deps) |
| `packages/trace_explorer/tests/test_explorer.py` | Update test fixtures to OTLP format, add viewer integration tests |

## Edge Cases

1. **Viewer unreachable:** `from_viewer()` raises `URLError` with clear message
2. **Empty session:** Same handling as empty file (no sessions found)
3. **Span not found:** `find_span()` falls back to raw span lookup, then error
4. **Large traces:** Pagination in `from_viewer()` handles any size; in-memory is OK
5. **Session ID resolution from span ID:** For `--span-id` without `--session-id`,
   we need the viewer to tell us which session a span belongs to. The current API
   doesn't support this directly — we may need to require `--session-id` alongside
   `--span-id`, or add a `/api/span/{span_id}/session` endpoint to the viewer.

## Open Question

- **`--span-id` without `--session-id`:** The viewer API has no endpoint to look up
  which session a span belongs to. Options:
  (a) Require `--session-id` when using `--span-id` (simplest)
  (b) The "Debug with Agent" button includes both in the prompt (no API change)
  (c) Add a new viewer endpoint for span→session lookup

  **Recommendation:** Option (b) — the button has all the info. For now, require
  `--session-id` with `--span-id`.

## Test Strategy

1. **Unit tests:** Create OTLP-format test fixtures. Test `_normalize_otlp_span()`
   conversion. Test `_load_spans()` with OTLP JSONL.
2. **Integration test:** Mock HTTP responses for `from_viewer()` pagination.
3. **CLI test:** Test argument parsing for new flags.
4. **Manual test:** Run against a live viewer instance.
