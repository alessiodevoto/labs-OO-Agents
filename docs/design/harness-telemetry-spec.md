# Harness Telemetry Spec

**Status:** Implementation spec  
**Issue:** #125  
**Replaces:** MR !47 (to be reimplemented from scratch)

## Problem

The framework silently "helps" the model in 30+ ways: stripping markdown fences,
fixing JSON escapes, translating unknown tool calls to `execute_python`, retrying
on validation errors, etc. Today this is invisible -- logged at DEBUG level but
not structured, not queryable, not aggregatable across runs.

We need per-generation-session telemetry that records every harness intervention,
flushes it to OTLP span attributes, and surfaces it in trace_explorer.

## Design Principles

1. **No noise in hot paths.** Instrumentation must not make business logic harder
   to read. One-line calls only: `hm.fence_removal(detail)` -- never a 2-line
   `if metrics :=` guard at every call site.
2. **No reverse dependencies.** `unifiedllm` must not import from
   `nemo_oo_agents`. Use a ContextVar-based callback protocol instead.
3. **No dead code.** Only define record methods that have callers. Add new ones
   when instrumenting new paths.
4. **Correlated data stays together.** Use structured sub-models (Pydantic)
   instead of parallel lists that can drift out of sync.
5. **Single source of truth for schema.** The Pydantic model drives both OTLP
   attribute generation and trace_explorer display. No manual mapping.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  actor.py  (_execute_with_generation / execute_nested)      │
│    start_harness_metrics()                                  │
│    ...                                                      │
│    flush_to_span()  /  restore_harness_metrics()            │
└────────────┬────────────────────────────────────────────────┘
             │ ContextVar: _harness_metrics_var
             ▼
┌─────────────────────────────────────────────────────────────┐
│  HarnessMetrics (Pydantic BaseModel)                        │
│    - Structured sub-models for correlated data              │
│    - .record_*() convenience methods                        │
│    - .to_span_attributes() → flat dict for OTLP            │
│    - .span_attribute_schema() → category/label metadata     │
└─────────────────────────────────────────────────────────────┘
             ▲
             │ hm = get_harness_metrics()
             │ (returns instance or _NullMetrics singleton)
┌────────────┴────────────────────────────────────────────────┐
│  Instrumented call sites                                    │
│    codeact.py, predict.py, pure_python.py,                  │
│    actor.py, code_validator.py, unifiedllm.py               │
│                                                             │
│  Always: hm = get_harness_metrics()                         │
│          hm.fence_removal("```python")                      │
│  (no if-guard needed — NullMetrics is a no-op)              │
└─────────────────────────────────────────────────────────────┘
```

### Null Object Pattern

`get_harness_metrics()` never returns `None`. Outside a generation session it
returns a module-level `_NullMetrics` singleton whose record methods are all
no-ops. This eliminates every `if metrics :=` guard at call sites.

```python
class _NullMetrics:
    """No-op stand-in when no generation session is active."""
    def __getattr__(self, name: str) -> Callable[..., None]:
        return _noop

def _noop(*args: Any, **kwargs: Any) -> None:
    pass
```

### UnifiedLLM Callback Protocol (no reverse dependency)

Instead of `unifiedllm` importing `harness_metrics`, we use a ContextVar
holding an optional callback. The agent framework sets the callback when
starting a generation session; unifiedllm calls it if present.

```python
# In unifiedllm (standalone, no nemo_oo_agents import):
from contextvars import ContextVar
from typing import Any, Callable

_llm_metrics_callback: ContextVar[Callable[[str, Any], None] | None] = ContextVar(
    "llm_metrics_callback", default=None
)

def _record_llm_metric(event: str, detail: Any = None) -> None:
    """Fire-and-forget metric recording. No-op if no callback set."""
    cb = _llm_metrics_callback.get()
    if cb is not None:
        cb(event, detail)
```

The agent framework (in `actor.py`) sets this ContextVar to a function that
dispatches to `HarnessMetrics`:

```python
from unifiedllm.unifiedllm import _llm_metrics_callback

def _make_llm_metrics_bridge(hm: HarnessMetrics) -> Callable[[str, Any], None]:
    dispatch = {
        "think_tag_extracted": lambda _: hm.think_tag_extracted(),
        "malformed_think_tag_fixed": lambda _: hm.malformed_think_tag_fixed(),
        "json_fence_removed": lambda _: hm.json_fence_removed(),
        "json_control_chars_removed": lambda _: hm.json_control_chars_removed(),
        "json_escape_fixed": lambda _: hm.json_escape_fixed(),
        "json_nested_extraction": lambda _: hm.json_nested_extraction(),
        "json_double_decoded": lambda _: hm.json_double_decoded(),
        "reasoning_as_structured_output": lambda _: hm.reasoning_as_structured_output(),
    }
    def bridge(event: str, detail: Any = None) -> None:
        handler = dispatch.get(event)
        if handler:
            handler(detail)
    return bridge
```

## HarnessMetrics Model

### Core Model (Pydantic)

```python
from pydantic import BaseModel, Field

class ErrorRecord(BaseModel):
    """A single correlated error record."""
    error_type: str
    message: str
    turn: int = 0
    code_preview: str = ""

class HarnessMetrics(BaseModel):
    """Harness telemetry for one generation session."""

    # ── Code Sanitization ──
    fence_removals: list[str] = Field(default_factory=list)
    xml_wrappers_stripped: list[str] = Field(default_factory=list)
    nested_wrapper_iterations: int = 0
    reasoning_calls_stripped: int = 0

    # ── Import Handling ──
    imports_stripped: list[str] = Field(default_factory=list)
    blocked_modules_removed: list[str] = Field(default_factory=list)

    # ── Response Format Fixups ──
    text_to_synthetic: int = 0
    content_prepended_as_reasoning: int = 0
    empty_responses: int = 0
    gpt4o_double_quote_fixes: int = 0
    variable_refs_resolved: list[str] = Field(default_factory=list)
    json_string_auto_parsed: list[str] = Field(default_factory=list)  # parse method names
    args_normalized: int = 0

    # ── Tool Call Translation ──
    tool_calls_translated: list[str] = Field(default_factory=list)

    # ── Return Value Handling ──
    explicit_return_auto_completed: int = 0
    implicit_return_transformed: int = 0

    # ── Error Recovery ──
    validation_errors: list[ErrorRecord] = Field(default_factory=list)
    predict_retries: list[str] = Field(default_factory=list)  # error summaries
    block_syntax_errors: list[str] = Field(default_factory=list)
    llm_api_errors: list[str] = Field(default_factory=list)

    # ── Content/Reasoning (unifiedllm) ──
    think_tags_extracted: int = 0
    malformed_think_tag_fixed: int = 0
    content_to_reasoning_fallback: int = 0
    reasoning_as_structured_output: int = 0

    # ── JSON Cleanup (unifiedllm) ──
    json_fence_removed: int = 0
    json_control_chars_removed: int = 0
    json_escape_fixed: int = 0
    json_nested_extraction: int = 0
    json_double_decoded: int = 0

    # ── Code Execution ──
    exec_python_total: int = 0
    exec_python_success: int = 0
    exec_errors: list[ErrorRecord] = Field(default_factory=list)

    # ── Code Validation ──
    missing_awaits_detected: list[str] = Field(default_factory=list)
    infinite_loops_detected: int = 0

    # ── Prefill ──
    prefill_type: str = ""
```

### Capping & Truncation

Constants:
- `_MAX_LIST_ITEMS = 20`
- `_MAX_STRING_CHARS = 500`
- `_MAX_CODE_PREVIEW_CHARS = 200`

Enforcement via a Pydantic `model_validator(mode="before")` is too heavy.
Instead, record methods enforce limits at write time (same as current MR,
this part was fine):

```python
def _cap(self, lst: list, value: str, limit: int = _MAX_STRING_CHARS) -> None:
    if len(lst) < _MAX_LIST_ITEMS:
        lst.append(value[:limit] + "..." if len(value) > limit else value)
```

### Record Methods (convenience, one per metric)

Each record method is a thin wrapper that appends/increments with capping.
Named as the field itself (no `record_` prefix) to keep call sites terse:

```python
# Call site reads as:
hm = get_harness_metrics()
hm.fence_removal("```python")        # not hm.record_fence_removal(...)
hm.exec_error(ErrorRecord(...))       # structured, no parallel-list drift
hm.think_tag_extracted()              # simple counter
```

The method names match the field names (or close variants) so call sites
read naturally and are greppable.

### to_span_attributes()

Generates flat `harness.*` OTLP attributes from the model. Only non-default
values are emitted.

```python
def to_span_attributes(self) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for info in self.span_attribute_schema():
        key = info["key"]
        value = info["value_fn"](self)
        if value:  # skip zeros, empty lists, empty strings
            attrs[key] = value
    return attrs
```

### span_attribute_schema() (classmethod)

Returns a list of dicts describing every span attribute: key, label,
category, and a callable to extract the value from a `HarnessMetrics`
instance. This is the **single source of truth** used by both
`to_span_attributes()` and `trace_explorer`.

```python
@classmethod
def span_attribute_schema(cls) -> list[dict[str, Any]]:
    return [
        {
            "key": "harness.fence_removal.count",
            "label": "Fence removals",
            "category": "Code Sanitization",
            "value_fn": lambda m: len(m.fence_removals),
        },
        {
            "key": "harness.fence_removal.details",
            "label": "Fence removal details",
            "category": "Code Sanitization",
            "value_fn": lambda m: m.fence_removals,
            "is_detail": True,  # hidden in summary, shown in detail view
        },
        # ... one entry per attribute
    ]
```

### flush_to_span()

```python
def flush_to_span(self) -> None:
    try:
        from opentelemetry import trace as otel_trace
        span = otel_trace.get_current_span()
        if not span or not span.is_recording():
            return
        for key, value in self.to_span_attributes().items():
            span.set_attribute(key, value)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[HARNESS_METRICS] flush failed: %s", e)
```

## ContextVar Lifecycle

Same pattern as the current MR (this part was correct):

```python
_harness_metrics_var: ContextVar[HarnessMetrics | _NullMetrics] = ContextVar(
    "harness_metrics", default=_NULL_METRICS  # module-level singleton
)

def get_harness_metrics() -> HarnessMetrics | _NullMetrics:
    return _harness_metrics_var.get()

def start_harness_metrics() -> tuple[HarnessMetrics, HarnessMetrics | _NullMetrics]:
    prev = _harness_metrics_var.get()
    metrics = HarnessMetrics()
    _harness_metrics_var.set(metrics)
    return metrics, prev

def restore_harness_metrics(prev: HarnessMetrics | _NullMetrics) -> None:
    _harness_metrics_var.set(prev)
```

Integration point in `actor.py`:

```python
# In _execute_with_generation and execute_nested:
_hm, _prev_hm = start_harness_metrics() if should_trace else (None, None)
# ... also set _llm_metrics_callback for unifiedllm bridge ...
try:
    result = await strategy.execute(self, call)
finally:
    if _hm is not None:
        _hm.flush_to_span()
        restore_harness_metrics(_prev_hm)
    # ... restore _llm_metrics_callback ...
```

## Instrumentation Points

### actor.py
| Location | Metric | Notes |
|----------|--------|-------|
| `_strip_blocked_modules` | `blocked_modules_removed` | **Keep dict comprehension.** Record after filtering by comparing lengths, not by rewriting the loop. |
| `execute_code` (import stripping) | `imports_stripped` | Compare code before/after `strip_redundant_imports()`. **Do NOT change `strip_redundant_imports` signature.** |
| `execute_code` (implicit return) | `implicit_return_transformed` | Where `implicit_return_added = True` is set. |
| `execute_code` (REPL globals) | Not tracked | Low value, high noise. Omit. |
| `_execute_with_generation` | Lifecycle start/flush | Start metrics, set unifiedllm bridge, flush in finally. |
| `execute_nested` | Lifecycle start/flush | Same pattern as above. |

### codeact.py
| Location | Metric |
|----------|--------|
| fence stripping (via `strip_code_fences` in `_handle_execute_python`) | `fence_removal` |
| `_prepend_reasoning` | `content_prepended_as_reasoning` |
| Text-to-synthetic path | `text_to_synthetic` |
| Empty response path | `empty_responses` |
| Tool call translation | `tool_calls_translated` |
| `_handle_block_syntax_error` | `block_syntax_errors` |
| LLM API error handler | `llm_api_errors` |
| `_handle_return_result` validation error | `validation_errors` (ErrorRecord) |
| `_handle_return_result` variable ref | `variable_refs_resolved` |
| `_handle_return_result` JSON auto-parse | `json_string_auto_parsed` |
| `_handle_return_result` GPT-4o fix | `gpt4o_double_quote_fixes` |
| `_handle_return_result` args normalize | `args_normalized` |
| `_handle_execute_python` exec result | `exec_python_total/success`, `exec_errors` |
| Explicit return auto-complete | `explicit_return_auto_completed` |
| `_run_prefill` | `prefill_type` |

### predict.py
| Location | Metric |
|----------|--------|
| `_strip_xml_wrapper` | `xml_wrappers_stripped` |
| Content-to-reasoning fallback | `content_to_reasoning_fallback` |
| Retry loop | `predict_retries` |

### pure_python.py
| Location | Metric |
|----------|--------|
| `_strip_code_fences` (delegates to shared `strip_code_fences`) | `fence_removal` |
| `_strip_xml_wrapper` | `xml_wrappers_stripped` |
| `_strip_wrappers` (nested) | `nested_wrapper_iterations` |
| `_strip_reasoning_calls` | `reasoning_calls_stripped` |

### code_validator.py
| Location | Metric |
|----------|--------|
| `visit_While` (infinite loop) | `infinite_loops_detected` |
| `visit_Call` (missing await) | `missing_awaits_detected` |

### unifiedllm.py (via callback, no import)
| Location | Metric |
|----------|--------|
| `_extract_think_tags` (normal) | `think_tag_extracted` |
| `_extract_think_tags` (malformed) | `malformed_think_tag_fixed` |
| `extract_and_parse_json` (fence) | `json_fence_removed` |
| `extract_and_parse_json` (control chars) | `json_control_chars_removed` |
| `extract_and_parse_json` (escape fix) | `json_escape_fixed` |
| `extract_and_parse_json` (nested) | `json_nested_extraction` |
| `_recursively_parse_json_strings` | `json_double_decoded` |
| Structured output reasoning fallback | `reasoning_as_structured_output` |

## trace_explorer Integration

`get_harness_telemetry` and `get_harness_telemetry_data` use
`HarnessMetrics.span_attribute_schema()` to drive display, rather than
maintaining a separate hardcoded mapping.

```python
def get_harness_telemetry_data(self, session_id=None):
    # ... collect gen_spans, merge harness.* attributes ...
    merged = self._merge_harness_attributes(gen_spans)
    return {"title": title, "metrics": merged}

def get_harness_telemetry(self, session_id=None):
    data = self.get_harness_telemetry_data(session_id)
    schema = HarnessMetrics.span_attribute_schema()
    # Group schema entries by category, look up values in data["metrics"]
    # Format as readable table
```

CLI: `--harness` flag, with `--json` support.

## What Changed vs. MR !47

| Issue | MR !47 | This spec |
|-------|--------|-----------|
| Inline `if metrics :=` guards | 30+ two-line blocks | Null object pattern -- one-liners, no guards |
| `unifiedllm` reverse import | `try: from nemo_oo_agents...` | ContextVar callback protocol |
| Dead methods | 7 unused `record_*` methods | Only methods with callers |
| Correlated list drift | Parallel lists for errors | `ErrorRecord` sub-model |
| `strip_redundant_imports` API change | Returns tuple | Signature unchanged; caller detects changes |
| Manual trace_explorer mapping | Hardcoded categories dict | Driven from `span_attribute_schema()` |
| Static booleans | `syntax_warning_suppressed=True` | Removed |
| REPL globals tracking | `record_repl_global_added` | Omitted (low value) |
| Method naming | `record_fence_removal()` | `fence_removal()` (terser) |
