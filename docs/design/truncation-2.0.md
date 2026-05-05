# Truncation 2.0 Design

**Branch:** `feat/truncation-2.0`  
**Issues:** gl-20, gl-41, gl-44, gl-54, gl-74, gl-89  
**Deferred:** gl-46 (tail() context manager) — removed from this branch

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

## Change 2: pformat — stream-based internals

### Goal

`pformat` currently builds strings bottom-up (each `_format_*` function returns a string to its caller). This means the entire formatted representation must exist in memory before the char cap can fire. Replacing string-building with stream writes allows `TruncatingStringIO` to bound memory *during* formatting — not after.

### New internal architecture

All internal `_format_*` functions (`_pformat`, `_format_value`, `_format_sequence`, `_format_dict`) accept a `stream` writer and write to it directly instead of returning strings.

```python
# Before
def _format_sequence(seq, *, max_length, ...) -> str:
    parts = []
    for item in seq:
        parts.append(_format_value(item, ...))
    return "[" + ", ".join(parts) + "]"

# After
def _format_sequence(seq, stream, *, max_length, ...):
    stream.write("[")
    for i, item in enumerate(head_items):
        if i > 0:
            stream.write(", ")
        _format_value(item, stream, ...)
    if tail_items:
        stream.write(f", ... {dropped} items not shown ..., ")
        for i, item in enumerate(tail_items):
            if i > 0:
                stream.write(", ")
            _format_value(item, stream, ...)
    stream.write("]")
```

Nested containers share the same `TruncatingStringIO`. Total output across the entire object tree is bounded by `max_chars`.

### Compact/expanded path

The compact trial (try single-line, fall back to multi-line if > 120 chars) formats into a temporary `io.StringIO`. If the result is ≤ 120 chars, write it to the main stream. Otherwise write the expanded format directly to the main stream. No "undo" needed.

```python
trial = io.StringIO()
_format_sequence_compact(seq, trial, ...)
if len(trial.getvalue()) < 120:
    stream.write(trial.getvalue())
else:
    _format_sequence_expanded(seq, stream, ...)
```

### Public API changes

`pformat(obj, *, max_length, max_string, max_depth, max_chars=None, ...)`:
- Removes `max_total_chars` and `_truncated_out` (gone with `_budget`)
- Adds `max_chars: int | None = None` — feeds into `TruncatingStringIO(limit=max_chars)` when set
- Internally: always uses a stream. If `max_chars=None`, uses `io.StringIO` (unlimited). Returns `stream.getvalue()`.

`pprint(obj, ...)`:
- Writes formatted output directly to stdout via the internal stream-based formatter
- No intermediate string built. Stdout is already `TruncatingStringIO` via `ContextVarStream` — bounded automatically.

### safe_pformat simplification

```python
def safe_pformat(obj, *, max_chars=_SAFE_PFORMAT_MAX_CHARS, **kwargs):
    if isinstance(obj, str):
        # string fast-path: head+tail directly (unchanged)
        ...
    # Non-strings: pformat now handles the char cap internally
    return pformat(obj, max_chars=max_chars, **kwargs)
```

The post-cap string slicing disappears entirely. `_budget` / `max_total_chars` disappear. `safe_pformat` becomes a thin wrapper that sets the `max_chars` cap and handles the string fast-path.

---

## Change 3: gl-74 — instrumentation cap

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

## Change 4: gl-44 — PredictStrategy safeguard

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

## Change 5: gl-89 — token-based context/event limits

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

### gl-73 implementation notes (from shell timeout MR, 2025)

During the shell timeout recovery work we investigated the "redirect output to
temp files" approach (used by OpenCode in production). Findings:

**How OpenCode does it** (`3p/opencode/internal/llm/tools/shell/shell.go`):

```bash
eval $command > /tmp/stdout-file 2> /tmp/stderr-file
echo $? > /tmp/status-file
pwd > /tmp/cwd-file
```

Output goes to files → zero Python memory blowup. They poll the status file
for completion. On timeout: `pgrep -P <shell_pid>` → SIGTERM each child.

**Recommended approach for our BashSession** (hybrid — no polling):

```bash
{command} > /tmp/oo_out_{id} 2> /tmp/oo_err_{id}
__ec=$?
echo {sentinel} $__ec     # sentinel on pipe (not redirected)
pwd 1>&2
echo {sentinel} 1>&2      # sentinel on pipe (not redirected)
```

This preserves our sentinel-based completion detection (no polling!) while
getting the memory safety of file output. The sentinel arrives instantly after
the command because nothing else flows on the pipe.

After the sentinel arrives, read head+tail from the output files (capped at
`MAX_OUTPUT_CHARS`). The command's output never enters Python memory.

**Quoting concern**: commands with special characters need escaping. OpenCode
uses `shellQuote()` (`'` → `'\''`). Alternatively: write the command to a
temp script file and `source` it.

**Cleanup**: `finally` block removes temp files. Orphans in `/tmp` (tmpfs)
are negligible and cleared on reboot.

**Bonus**: enables a future "view full output" feature — expose the temp file
path so agents can `shell.view()` large outputs selectively instead of getting
a truncated blob in the return value.

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
| `tail()` stdout capture | TailingStringIO | ⏭ gl-46 deferred |
| Event serialization return values | safe_pformat | ✅ gl-74 |
| Trace span `returned_value` | safe_pformat + cap | ✅ gl-74 |
| Per-block context truncation | block_limit chars | ✅ existing |
| Total context budget | count_tokens function | ✅ gl-89 |
| Total event budget | count_tokens function | ✅ gl-89 |
