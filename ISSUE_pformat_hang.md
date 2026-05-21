# TUI hangs permanently on ESC — `_pformat` blocks event loop with synchronous I/O

## Summary

The TUI becomes permanently unresponsive when the tracing hook's `truncating_pformat()` encounters certain objects in the execution result. The serializer triggers expensive synchronous `os.stat()` calls (via `importlib_metadata`) that block the asyncio event loop indefinitely. **ESC cannot interrupt it** because there are no await points during the synchronous operation.

## Reproduction

1. Start a TUI session: `nemo oo tui`
2. Have the agent generate code that creates `importlib.metadata` objects:
   ```python
   from importlib.metadata import entry_points
   eps = entry_points(group="nemo_oo_agents.skills")
   print("Registered skill entry points:")
   for ep in eps:
       print(f"  {ep.name} → {ep.value}")
   ```
3. The TUI hangs with spinner running indefinitely
4. ESC prints "✗ Interrupted." but the spinner never stops
5. Multiple ESC presses produce multiple "✗ Interrupted." messages but the hang persists

## Root Cause

The tracing hook `after_code_execution` calls `_safe_serialize(result)` on the `ExecutionResult` object. `_safe_serialize` delegates to `truncating_pformat()` which recursively walks the object graph.

### The full call chain:

```
after_code_execution (tracing hook)
→ _safe_serialize(ExecutionResult)
→ truncating_pformat(ExecutionResult)
→ _format_instance_repr → walks ExecutionResult fields
→ finds captured_locals: {'eps': EntryPoints(...)} [a dict field]
→ _format_dict → iterates dict items
→ _format_nested_instance(EntryPoint) → _extract_instance_values
→ getattr(entrypoint, 'files') OR hasattr(entrypoint, 'files')
→ triggers importlib_metadata Distribution.files property
→ skip_missing_files(package_paths)
→ filter(lambda path: path.locate().exists(), package_paths)
→ os.stat() on EVERY file listed in the package's RECORD
```

### Why ESC cannot fix it:

- `os.stat()` is a **synchronous C call** — no Python bytecodes execute between individual stat calls
- `asyncio.Task.cancel()` only delivers `CancelledError` at **await points**
- Since there are no await points in the stat loop, the cancellation is queued but never delivered
- The event loop is completely blocked on the agent-loop thread

### Evidence from live debugging:

```python
# Coroutine state inspection from IPython (different thread):
coro = handle_task.get_coro()
print(f"cr_running: {coro.cr_running}")   # True — actively executing sync code
print(f"cr_await: {coro.cr_await}")       # None — no await point to cancel at
```

Native thread stack trace (via `sys._current_frames()`):
```
nemo_oo_agents/agentdoc/_pformat.py:1112  in _extract_instance_values
importlib_metadata/__init__.py:605        in files
importlib_metadata/__init__.py:603        in skip_missing_files
    filter(lambda path: path.locate().exists(), package_paths)
pathlib/_abc.py:458                       in exists
pathlib/_local.py:517                     in stat
    os.stat(self, follow_symlinks=follow_symlinks)
```

## Two Contributing Bugs

### Bug 1: `_pformat` ignores Pydantic's `exclude=True`

`ExecutionResult.captured_locals` is annotated with `Field(exclude=True)` — it should never be serialized. But `_pformat` has two paths that ignore this:

1. **`_format_instance_repr`** iterates `type_info.fields` (which includes excluded fields) and calls `_format_value_to_str` on each value, which eventually recurses into the dict.
2. **`_extract_instance_values`** reads from `__dict__` directly, bypassing Pydantic's exclusion logic.

**Fix**: `_extract_instance_values` and `_format_instance_repr` should respect `model_fields[name].exclude == True` and skip those fields entirely.

### Bug 2: `_pformat` triggers expensive property descriptors via `hasattr()`/`getattr()`

Even without `captured_locals`, if `_pformat` encounters any `importlib_metadata` object (e.g., if an agent returns one directly), `_extract_instance_values` calls `hasattr(obj, field.name)` which triggers Python's descriptor protocol, invoking properties that do arbitrary I/O.

The `Distribution.files` property iterates every file in a package's RECORD and calls `stat()` on each. On venvs with large packages (e.g., torch, transformers), this can be thousands of stat calls.

**Fix options**:
- For tracing serialization: use `repr()` for non-Pydantic/non-dataclass types (like Rich does) instead of deep attribute introspection
- Guard `_extract_instance_values`: check if a field is a property descriptor on the class before calling `hasattr`/`getattr`, or only read from `__dict__` for unknown types
- Add a wall-clock timeout to `truncating_pformat` when called from tracing hooks

## Impact

- **Severity**: High — TUI becomes permanently unresponsive, requires process kill
- **Frequency**: Triggers whenever the agent generates code that imports `importlib.metadata` and stores results in locals (common during skill discovery/introspection)
- **User experience**: ESC appears to work (prints "✗ Interrupted.") but the spinner never stops. Users have no way to recover without killing the process.

## Interaction with gl-212 sentinel

The gl-212 fix added a sentinel task (`await asyncio.Future()`) to keep the agent loop alive across dispatcher restarts. Before gl-212, the loop would die after cancellation and be recreated fresh. With the sentinel, the loop persists — which means the blocked coroutine continues consuming the thread indefinitely. Without gl-212, the loop death might have eventually freed the thread (though the hang would still occur during the current turn).

## Proposed Fix

### Immediate (targeted):

1. In `_extract_instance_values` and `_format_instance_repr`: skip fields where `obj_type.model_fields[field_name].exclude == True`
2. This prevents `captured_locals` from being serialized, which is the primary trigger

### Defense-in-depth:

3. Run `truncating_pformat` in the tracing hook via `asyncio.to_thread()` or with a timeout wrapper, so even if it blocks, the event loop remains responsive to cancellation
4. Consider using `repr()` as the fallback for types that aren't Pydantic models or dataclasses (matching Rich's approach) when called from tracing/serialization contexts

## Workaround

Kill the process and restart. The hang resolves once the stat loop completes (which can take minutes on large venvs).

## Environment

- Python 3.13.7
- nemo-oo-agents (editable install from main branch)
- importlib_metadata (backport, not stdlib)
- Large venv with many packages

