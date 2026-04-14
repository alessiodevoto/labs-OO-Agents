# Truncation 2.0 Design

**Branch:** `feat/truncation-2.0`  
**Issues:** gl-20, gl-41, gl-44, gl-46, gl-54, gl-74, gl-89

---

## Guiding principle

Two mechanisms. They compose, they don't compete.

- **M1 (`pformat`)** — structural, developer-driven. Decides *which elements* to show. Operates on container element counts, string length, and nesting depth.
- **M2 (`safe_pformat`, `TruncatingStringIO`)** — automatic safety net. Decides *how many chars* to keep. Type-oblivious. Fires without the caller doing anything.

These are **independent axes**. M1 is not responsible for memory safety. M2 is not responsible for structural layout.

---

## Change 1: pformat — remove _budget

### Current state

`_pformat`, `_format_value`, `_format_sequence`, `_format_dict` all thread a `_budget: list[int] | None` parameter. When the budget exhausts during iteration, they append `... +N more` and stop. `pformat()` public API accepts `max_total_chars` and `_truncated_out`.

This conflates two concerns: structural layout (M1) and memory safety (M2). The `_budget` machinery has ~40 references scattered across the internal formatter, interacts with the compact/expanded path decision, and produced a pre-existing bug where the dict expanded path under-deducts budget (gl-118).

### New state

Remove `_budget`, `max_total_chars`, and `_truncated_out` from all internal functions and from the public `pformat()` API.

`pformat` becomes purely structural: `max_length`, `max_string`, `max_depth`. No char tracking internally.

**Memory implication:** Without `_budget`, pformat can produce larger strings than before for large inputs. This is intentional — memory safety is M2's job. Callers who need a hard char cap call `safe_pformat`, not `pformat` directly.

**Element-aware head+tail stays:** `_format_sequence` and `_format_dict` already implement head+tail for ordered containers based on `max_length` (ceil/floor split). This is independent of `_budget` and is unchanged.

---

## Change 2: safe_pformat — use TruncatingStringIO

### Current state

```python
text = pformat(obj, max_total_chars=max_chars, _truncated_out=truncated_out, **kwargs)
if len(text) > max_chars:
    # String slicing post-cap — can produce broken Python syntax
    return head_notice + text[:n_head] + notice + text[-n_tail:]
```

### New state

```python
stream = TruncatingStringIO(limit=max_chars)
text = pformat(obj, **kwargs)
stream.write(text)
return stream.getvalue()
```

`TruncatingStringIO` already exists (`truncating_stream.py`). It keeps a fixed head buffer and a rolling circular tail buffer. `getvalue()` returns head+tail with a prose notice when truncated.

The string fast-path (plain strings) is unchanged — strings bypass pformat entirely and get head+tail applied directly.

**Memory safety note:** pformat still builds the full string in memory before handing it to `TruncatingStringIO`. This is not a regression — the current approach also builds the string in memory. The improvement is in output quality: `TruncatingStringIO` produces valid head+tail with a clean prose notice, whereas the current string slicing chops mid-repr and can produce broken Python syntax. Memory safety remains the caller's responsibility via reasonable structural limits (`max_length`, `max_string`, `max_depth`).

---

## Change 3: gl-46 — tail() context manager

### Motivation

Agents want to see only the end of long computations:

```python
with tail(25_000):
    some_long_running_loop()
```

### Implementation

Stdout during code execution is captured via `ContextVarStream` + `_stdout_buffer_var` (a ContextVar), **not** via `sys.stdout` replacement. `contextlib.redirect_stdout` would be the wrong mechanism — it only intercepts `sys.stdout` attribute access, but `ContextVarStream.write()` reads the ContextVar directly, bypassing any `sys.stdout` swap.

**`tail(chars)` context manager** — temporarily replaces `_stdout_buffer_var` with a tail-only buffer, then on exit forwards the captured tail to the outer buffer.

```python
@contextmanager
def tail(chars: int = 25_000):
    buf = TruncatingStringIO(limit=chars, tail_chars=chars)  # head_limit=0 → tail-only
    token = _stdout_buffer_var.set(buf)
    try:
        yield
    finally:
        _stdout_buffer_var.reset(token)
        outer = token.old_value
        if outer is not None:
            outer.write(buf.getvalue())
```

`TruncatingStringIO(limit=N, tail_chars=N)` gives `head_limit=0` — all writes go directly to the rolling tail buffer. No new class needed; `TruncatingStringIO` is reused with this configuration.

`_stdout_buffer_var` must be importable from wherever `tail()` is defined. Move it (or re-export it) from `actor.py` to `stream_wrappers.py` so `tail()` can import it without circular dependencies.

Add `tail` to `codeact_exec_globals` alongside `pprint` and `doc`.

---

## Change 4: gl-74 — instrumentation cap

### Current state

`_safe_serialize_execution_result` in `_hooks_impl.py` calls `safe_pformat(rv)` on `returned_value`, then JSON-serializes the dict, then applies a crude `s[:50_000]` string slice if the JSON is still too large. The post-JSON slicing produces invalid JSON.

### New state

Cap `returned_value` before JSON serialization with an explicit `max_chars`:

```python
d["returned_value"] = safe_pformat(rv, max_chars=50_000)
```

Remove the post-JSON slicing entirely. `safe_pformat` already returns a prose-noticed truncated string when needed. The JSON will be valid.

All other `_safe_serialize` calls (for `agent.args`, `agent.kwargs`, `method.result`, etc.) should similarly ensure they use `safe_pformat` with an explicit cap, not unbounded `repr()`.

---

## Change 5: gl-44 — PredictStrategy safeguard

### Current state

`_build_task_message` embeds parameter values via `format_parameters_as_code()`, which uses `repr()`. Large parameters (500KB strings, deeply nested objects) go into the prompt verbatim and are truncated later at the block level — silently, mid-value, with no notice.

### New state

`format_parameters_as_code` currently takes no parameters and uses `{value!r}` directly. Add an optional `value_formatter` parameter:

```python
def format_parameters_as_code(
    self,
    value_formatter: Callable[[Any], str] = repr,
) -> str:
```

`PredictStrategy._build_task_message` passes `safe_pformat` as the formatter:

```python
original_call.format_parameters_as_code(
    value_formatter=lambda v: safe_pformat(v, max_chars=max_param_chars)
)
```

`max_param_chars` default: 3,000 chars per parameter (configurable via `TruncationConfig`). 10,000 is too permissive — a 5-parameter method at 10KB each = 50KB in the prompt before context blocks are considered.

The default remains `repr` for all other callers (`pure_python.py` etc.) so existing behaviour is unchanged.

---

## Change 6: gl-89 — token-based context/event limits

### Current state

`TruncationConfig.max_context_tokens` and `max_event_tokens` are named for tokens but enforced via `count_tokens` function passed to `render_context()`. If `count_tokens=None` and limits are set, `render_context()` raises `ValueError`. This means token limits are unusable unless the caller manually provides a token counter.

### New state

`actor.py` validates at agent startup that a token counter is available when limits are configured (line ~1679). Currently this raises `ValueError` if `count_tokens` is not provided. Instead, fall back to a char-based approximation:

```python
def _get_token_counter(llm) -> Callable[[str], int]:
    if hasattr(llm, "count_tokens"):
        return llm.count_tokens
    logger.warning(
        "max_context_tokens / max_event_tokens set but LLM has no count_tokens; "
        "using char approximation (÷4). Token limits may be inaccurate."
    )
    return lambda text: len(text) // 4  # ~4 chars/token for English
```

Pass this to `render_context()` whenever `max_context_tokens` or `max_event_tokens` is set, replacing the current hard `ValueError`. The warning surfaces the approximation so operators can wire up a real tokenizer if precision matters.

---

## What is NOT in scope

- **gl-73** (escape hatch for very large stdout outputs — write-to-file): deferred.
- Timeout for pathological `__repr__` that hangs or allocates unboundedly: deferred.
- Changing `TruncatingStringIO` itself: it already works correctly.

---

## TODO: Full codebase truncation audit

Before finalising the audit checklist, search the entire codebase for all truncation points:
- All calls to `pformat`, `safe_pformat`, `repr()`, `str()`, `json.dumps()` on potentially large objects
- All uses of `TruncatingStringIO`, `_safe_serialize`, string slicing on output
- All places where output flows to the LLM (prompts, context blocks, event serialization) or into traces (span attributes)

For each site, decide: M1 (structural pformat), M2 (safe_pformat / TruncatingStringIO), both, or neither (already bounded). This audit drives the final version of the table below.

## Audit checklist (gl-41)

After all changes above, verify each site in the pipeline:

| Site | Mechanism | Status after this branch |
|------|-----------|--------------------------|
| LLM-generated `pprint()` | pformat M1 (max_length/string/depth) | ✅ head+tail via max_length |
| `prefill.py` parameter inspection | pformat M1 | ✅ same |
| `PredictStrategy` parameter embedding | safe_pformat cap | ✅ gl-44 |
| stdout/stderr from code execution | TruncatingStringIO | ✅ existing |
| `tail()` stdout capture | TailingStringIO | ✅ gl-46 |
| Event serialization return values | safe_pformat | ✅ gl-74 |
| Trace span `returned_value` | safe_pformat + cap | ✅ gl-74 |
| Per-block context truncation | block_limit chars | ✅ existing |
| Total context budget | count_tokens function | ✅ gl-89 |
| Total event budget | count_tokens function | ✅ gl-89 |
