# Issue 181 — Trace sync (`def`) methods on Agent subclasses

GitLab: https://gitlab-master.nvidia.com/interactive-agents/nooa/-/issues/181

## Problem

`AgentMeta.__new__()` only wraps `async def` methods (see `metaclass.py:64`):

```python
if not inspect.iscoroutinefunction(attr_value):
    continue
```

Any sync `def` method on an `Agent` subclass is silently skipped, so it produces zero
trace spans even when `_enable_tracing = True`. This makes deterministic helpers
(e.g. `_search`, `_scrape`, `_gather_sources` in the deep researcher use case)
invisible to the analyzer that drives the evolutionary optimizer.

## Goal

When a subclass of `Agent` (i.e. a class with `_enable_tracing = True`) defines a sync
`def` method, calls to it must produce `before_agent_call` / `after_agent_call`
hook events with the right `call_id`/`parent_call_id`/`source_code` attributes — so
the OTel exporter (and other instrumentation) emits AGENT spans identical in shape
to the ones produced for async methods today.

Non-goals:

- LLM **generation** for sync methods. Generation is fundamentally async (LLM calls,
  middleware, runtime queueing). Sync methods with ellipsis bodies remain "no-op
  body returns `None`", which is the current behavior; we just don't make it worse.
- Running `agent_call` middleware around sync methods. Middleware is async; sync
  methods skip it. Documented in code.

## Design

### 1. New sync wrapper in `runtime/method_wrapper.py`

Add `create_sync_agent_method_wrapper(original_func, *, needs_tracing, cached_source_code)`
alongside the existing async `create_agent_method_wrapper`. It returns a **sync**
`functools.wraps` wrapper.

Like the async wrapper, expose a mutable `_tracing_enabled = [needs_tracing]`
attribute on the wrapper so `@no_trace` applied as the outer decorator can flip
the flag retroactively (this matches the async path, see `metaclass.py:no_trace`).

Wrapper logic:

1. **Fast path when no runtime yet.** If `not hasattr(self, "runtime")`, just
   `return original_func(self, *args, **kwargs)` directly with no hook firing
   and no stack manipulation. This mirrors the async wrapper's
   `elif not needs_generation: return await original_func(...)` fall-through and
   is essential because `Agent.__init__` calls sync helpers (`_resolve_llm`,
   `_resolve_truncation`, `_resolve_event_query`, `_apply_context_dict`,
   `_system_prompt`) **before** `self.runtime` is assigned.
2. **With runtime.** Generate `call_id = str(uuid4())`. Read
   `parent_call_id = self.runtime._agent_call_id` — same source the async wrapper
   uses (method_wrapper.py:110), so parent linkage is identical.
3. Build trace attributes via the existing helper:
   `_build_trace_attributes(needs_generation=False, strategy=None, cached_source_code=cached_source_code)`.
   Reusing this guarantees sync hook kwargs are byte-identical to the async
   non-generation path that `test_regular_methods_capture_source_code` verifies.
4. Push to the stack: `_push_agent_call_id(call_id if _tracing_enabled[0] else parent_call_id)`.
   Identical semantics to the async wrapper at method_wrapper.py:137 — `@no_trace`
   methods propagate the parent's id so children find the nearest traced ancestor.
5. If `_tracing_enabled[0]`: `hook_context = call_before_hook("before_agent_call", ...)`
   with the same kwargs the async wrapper uses (`agent`, `method_name`, `args`,
   `kwargs`, `call_id`, `parent_call_id`, plus `**trace_attrs`).
6. `try: result = original_func(self, *args, **kwargs)` — synchronously.
7. `except Exception as e: exception_caught = e; raise`.
8. `finally:` `_pop_agent_call_id()`. If `hook_context is not None`, call
   `call_after_hook("after_agent_call", hook_context, agent=self,
   method_name=..., result=result, exception=exception_caught)`.

Differences vs the async wrapper, by design:

- **No `agent_call` middleware.** Middleware is async. Sync methods can't await
  it; running it would change the calling convention. Document this in the
  wrapper docstring. (If a middleware is registered, it simply doesn't run for
  sync helpers — same as before this fix, since sync methods weren't wrapped at
  all.)
- **No generation path.** Sync methods can't generate.
- **No `_parent_agent_var.set` / scoped-blocks clearing.** Those isolate context
  across async **agent boundaries**. Sync helpers run inside the calling agent's
  async frame; the parent agent var is already set by the async caller.

The wrapper attaches: `_agent_decorator = "auto"`, `_needs_generation = False`,
`_plan_strategy = None`, `_tracing_enabled = [needs_tracing]`, `_original = func`
so introspection (and `@no_trace` applied after) keeps working.

### 2. Update `metaclass.py:AgentMeta.__new__`

Replace the early `continue` for non-coroutines. New control flow per attribute:

```
if attr_value is async function:
    (existing logic — unchanged)
elif inspect.isfunction(attr_value):  # sync def
    if attr_name is dunder (starts AND ends with "__"):
        continue   # leave __init__/__init_subclass__/__custom__ etc alone
    if hasattr(attr_value, "_agent_decorator"):
        continue   # already wrapped (e.g. by @strategy)
    should_trace = mcs._should_trace(attr_name, attr_value, should_trace_class)
    if not should_trace:
        continue
    cached_source_code = AgentMeta._extract_source_code(attr_value)
    wrapped = create_sync_agent_method_wrapper(
        attr_value,
        needs_tracing=True,
        cached_source_code=cached_source_code,
    )
    setattr(wrapped, "_original", attr_value)
    type.__setattr__(cls, attr_name, wrapped)
else:
    continue   # staticmethod, classmethod, property, descriptors — skip
```

Why skip dunders for sync but not for async:

- The async path has been tracing custom dunders for a while (see
  `test_dunder_ellipsis_generated_and_traced`); changing that would be a behavior
  regression.
- Sync dunders include `__init__`, `__init_subclass__`, `__setattr__`,
  `__getattribute__`, `__class_getitem__`. Wrapping any of them risks infinite
  recursion (tracing reads attributes, which fires `__getattribute__`, which
  triggers tracing…) or running before the runtime exists.
- We use a name-based filter (`startswith("__") and endswith("__")`) — narrow,
  conservative, easy to reverse if a real use case appears.

Why properties / classmethods / staticmethods are skipped automatically:

- `inspect.isfunction()` returns False for `property`, `classmethod`, and
  `staticmethod` descriptor objects (they are not plain functions). The
  `elif inspect.isfunction(attr_value):` guard naturally excludes them.
- Concretely on the `Agent` base class itself: `agent_id` (`@property`),
  `context_stats` (`@property`), `__type_info__` (`@classmethod`) are skipped
  by this filter; `__setattr__` and `__instance_values__` are skipped by the
  dunder filter.

### 3. Update existing test that expects sync methods to NOT be wrapped

`tests/test_metaclass.py::test_sync_methods_not_wrapped` currently asserts the
opposite of the new behavior. Update it: sync methods on an `Agent` subclass (which
has `_enable_tracing = True`) **should** now have `_agent_decorator == "auto"`.

### 4. New tests in `tests/test_metaclass.py`

Add:

- `test_sync_method_traced_when_enable_tracing` — install mock hooks, define
  `class TestAgent(Agent, llm=...)` with `def sync_method(self, x)` returning
  `x*2`, call it, verify `before_agent_call` + `after_agent_call` fired with the
  expected kwargs and that the return value is `42` (not a coroutine).
- `test_sync_method_returns_value_directly_not_coroutine` — `result = agent.fn(1)`
  is the int, never a coroutine. (Belt-and-suspenders against an accidental
  async wrapper.)
- `test_sync_method_source_code_captured` — `source_code` kwarg in
  `before_agent_call` contains the literal method body.
- `test_sync_method_no_trace_decorator_suppresses_hooks` — `@no_trace` on a sync
  method suppresses both hooks.
- `test_sync_method_not_traced_when_class_does_not_enable_tracing` — define a
  bare `class C(metaclass=AgentMeta)` (no `Agent` base, so `_enable_tracing` is
  `False`); sync methods are not wrapped.
- `test_sync_dunder_methods_not_wrapped` — `__custom__` sync dunder is not wrapped.
- `test_sync_classmethod_and_staticmethod_not_wrapped` — `@classmethod` /
  `@staticmethod` on a user `Agent` subclass are not wrapped.
- `test_sync_property_not_wrapped` — `@property` is not wrapped (descriptor).
- `test_sync_child_of_async_parent_has_correct_parent_call_id` — async
  `orchestrator` calls sync `helper`; verify the sync helper's `parent_call_id`
  equals the orchestrator's `call_id`.
- `test_sync_method_inheritance` — subclass that overrides a parent's sync method
  gets its own wrapper.
- `test_sync_method_already_decorated_skipped` — a sync method that already has
  `_agent_decorator` is not re-wrapped.
- `test_agent_init_succeeds_with_sync_tracing_active` — install mock hooks, then
  instantiate any plain `class TestAgent(Agent, llm=...)`. Construction must
  succeed (proves the runtime-missing fast path works for `_resolve_llm`,
  `_resolve_truncation`, `_resolve_event_query`). Verify hooks are NOT called
  for these helpers (because `runtime` doesn't exist yet at that point).

### 5. Verification

- Run `uv run pytest tests/test_metaclass.py tests/tracing/ tests/integration/test_concurrent_traces.py tests/integration/test_hook_failure_traces.py -q`.
- Run `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`.

## Files touched

- `src/nooa/runtime/method_wrapper.py` — add `create_sync_agent_method_wrapper`.
- `src/nooa/metaclass.py` — extend `__new__` loop with sync branch.
- `tests/test_metaclass.py` — update one existing test, add new tests listed above.

## Risk

- A subclass that overrides a sync method already wrapped on the parent now gets
  its own wrapper at child class creation time. The async path already does this
  (`test_method_inheritance`); the new sync path mirrors it. Correct.
- Hooks are defensively wrapped in `call_before_hook` / `call_after_hook`, so a
  buggy hook still doesn't crash sync method calls — same guarantee as async.
- Performance impact when `_enable_tracing = True` but no hooks installed: one
  extra `get_hooks()` (a context-var read, ~ns) plus push/pop on a contextvar
  tuple. Negligible per call.
