# The Case of the Missing Trace Files

## Problem Statement

On the `history-phases-1-3` branch, trace files are not being written for some test executions. This does NOT happen on `main`.

When traces go missing, you see warnings like:

```
WARNING:eval_pipeline.pipeline:Trace file results/capability_optimization_20260126_142013/traces/EmployeeSalaryAgent_claude-sonnet_get_employee_salary_employee_lookup_20260126_142013_06_000000_claude-sonnet.006trace.jsonl does not exist or is empty for sample employee_lookup_001_claude-sonnet_run6. This may indicate spans were not exported correctly.
```

## Observations

1. **Branch-specific**: Only happens on `history-phases-1-3`, not on `main`
2. **Run-dependent**: Appears in later runs (run6+), not in early runs (run1-5)
3. **Cross-model**: Affects multiple models (claude-sonnet, gemini-2.5-flash-lite, nemotron3-nano-30b, etc.)
4. **Cross-test**: Affects multiple test types (employee_lookup, router tests, etc.)
5. **No subprocess**: User confirmed NOT using subprocess execution mode

## Sample Error Output

```
  ✗ [1714/2940] 2053.3s (  3.0s) W14 employee_lookup/employee_lookup_001/gpt-oss-120b/run6 — 1191 passed (58%)
WARNING:eval_pipeline.pipeline:Trace file results/capability_optimization_20260126_142013/traces/EmployeeSalaryAgent_claude-sonnet_get_employee_salary_employee_lookup_20260126_142013_06_000000_claude-sonnet.006trace.jsonl does not exist or is empty for sample employee_lookup_001_claude-sonnet_run6. This may indicate spans were not exported correctly.
  ✗ [1715/2940] 2053.6s (  1.5s) W15 employee_lookup/employee_lookup_001/claude-sonnet/run6 — 1191 passed (58%)
WARNING:eval_pipeline.pipeline:Trace file results/capability_optimization_20260126_142013/traces/EmployeeSalaryAgent_nemotron3-nano-30b_get_employee_salary_employee_lookup_20260126_142013_06_000000_nemotron3-nano-30b.006trace.jsonl does not exist or is empty for sample employee_lookup_001_nemotron3-nano-30b_run6. This may indicate spans were not exported correctly.
  ✗ [1716/2940] 2054.9s (  2.3s) W20 employee_lookup/employee_lookup_001/nemotron3-nano-30b/run6 — 1191 passed (58%)
WARNING:eval_pipeline.pipeline:Trace file results/capability_optimization_20260126_142013/traces/EmployeeSalaryAgent_gemini-2.5-flash-lite_get_employee_salary_employee_lookup_20260126_142013_06_000000_gemini-2.5-flash-lite.006trace.jsonl does not exist or is empty for sample employee_lookup_001_gemini-2.5-flash-lite_run6. This may indicate spans were not exported correctly.
  ✗ [1717/2940] 2055.5s (  2.3s) W14 employee_lookup/employee_lookup_001/gemini-2.5-flash-lite/run6 — 1191 passed (58%)
  ✓ [1718/2940] 2056.7s ( 19.1s) W17 fast_food_cancel/fast_food_cancel_001/claude-sonnet/run6 — 1192 passed (58%)
WARNING:eval_pipeline.pipeline:Trace file results/capability_optimization_20260126_142013/traces/EmployeeSalaryAgent_claude-haiku_get_employee_salary_employee_lookup_20260126_142013_06_000000_claude-haiku.006trace.jsonl does not exist or is empty for sample employee_lookup_001_claude-haiku_run6. This may indicate spans were not exported correctly.
```

## Results Directory

```
results/capability_optimization_20260126_142013/
```

## Tracing Architecture

### Key Files

1. **Exporter**: `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_jsonl_exporter.py`
   - `JSONLSpanExporter` class writes spans to JSONL files
   - Uses `_current_trace_file` ContextVar for per-sample file routing
   - Caches file handles in `self._files` dict with `threading.Lock`

2. **Initialization**: `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/__init__.py`
   - `enable_tracing()` - called once at startup in main process
   - `set_trace_file()` / `get_trace_file()` - per-sample file routing via ContextVar
   - `Agent006Instrumentor` - installs hooks via `set_hooks()`

3. **Hooks**: `src/nemo_oo_agents/runtime/hooks.py`
   - `_instrumentation_hooks_var` - ContextVar storing hooks
   - `set_hooks()` / `get_hooks()` - hook installation
   - `call_before_hook()` / `call_after_hook()` - span creation/ending

4. **Pipeline**: `util/eval_pipeline/src/eval_pipeline/pipeline.py`
   - `process_sample()` - sets trace file, runs agent, checks trace exists
   - Line ~234: `set_trace_file(trace_file)`
   - Line ~315-324: Warning when trace file missing/empty
   - Line ~330: `exporter.close_file(trace_file)`

5. **Evaluator**: `util/eval_pipeline/src/eval_pipeline/evaluator.py`
   - Calls `enable_tracing()` at startup

### Execution Flow

```
1. evaluator.py: enable_tracing() → creates TracerProvider, SpanProcessor, JSONLSpanExporter
2. evaluator.py: Agent006Instrumentor().instrument() → calls set_hooks()
3. pipeline.py: run_evaluation() starts
4. For each sample:
   a. pipeline.py: set_trace_file(trace_file) → sets ContextVar
   b. pipeline.py: agent = sample.agent_factory()
   c. pipeline.py: execute_task(agent, ...) → runs agent method
   d. [hooks create spans, exporter writes to file via get_trace_file()]
   e. pipeline.py: check if trace_file exists and has content
   f. pipeline.py: exporter.close_file(trace_file)
```

### Key ContextVars

1. `_current_trace_file` (in _jsonl_exporter.py) - routes spans to correct file
2. `_instrumentation_hooks_var` (in hooks.py) - stores active hooks
3. `_parent_agent_var` (in agent.py) - for LLM inheritance (separate issue)

## Branch Changes

The `history-phases-1-3` branch has these relevant changes:

```bash
git log --oneline main..HEAD
# 67ac3fe1 Fix circular import causing NemoOOAgentsProvider not to register
# 88de539c feat: history context management system design and phase 1-3 implementation
# eca70675 refactor(context-blocks): make BlockRenderer async-only
# edc80f3d refactor: add Role enum to RenderSpec for declarative event role mapping
# 53888fd4 feat: history context management phases 1-3
```

Key changed files:
- `src/nemo_oo_agents/events.py` - Event structure changes
- `src/nemo_oo_agents/runtime/history.py` - History management changes
- `packages/context-blocks/src/context_blocks/formatter.py` - Formatter changes
- `packages/context-blocks/src/context_blocks/renderer.py` - Renderer changes

## Investigation Tasks

### 1. Verify the Problem Scope

```bash
# Count missing trace files in results
ls -la results/capability_optimization_20260126_142013/traces/ | wc -l

# Check which traces exist vs which are in the JSONL results
# Compare trace_file paths in .006eval.jsonl to actual files
```

### 2. Check Hook Installation

Add debug logging to verify hooks are installed when samples run:

```python
# In pipeline.py before execute_task()
from nemo_oo_agents.runtime.hooks import get_hooks
print(f"DEBUG: hooks installed = {get_hooks() is not None}")
```

### 3. Check ContextVar Propagation

The hooks use a ContextVar. Verify it's accessible during agent execution:

```python
# In _jsonl_exporter.py export() method
print(f"DEBUG: get_trace_file() = {get_trace_file()}")
print(f"DEBUG: self.trace_file = {self.trace_file}")
```

### 4. Check Span Creation

Verify spans are being created at all:

```python
# In _hooks_impl.py before_ellipsis_method()
print(f"DEBUG: Creating span for {method_name}")
```

### 5. Check File Handle State

The exporter caches file handles. Check for accumulation or errors:

```python
# In _jsonl_exporter.py _get_file()
print(f"DEBUG: File cache size = {len(self._files)}")
print(f"DEBUG: Opening file = {path}")
```

### 6. Compare with Main Branch

Run the same evaluation on `main` branch to confirm this is branch-specific:

```bash
git stash
git checkout main
# Run same evaluation
git checkout history-phases-1-3
git stash pop
```

### 7. Bisect the Commits

If branch-specific, bisect to find the breaking commit:

```bash
git bisect start
git bisect bad HEAD
git bisect good main
# Run evaluation, mark good/bad
```

## Hypothesis

The most likely causes are:

1. **ContextVar not propagating** - Hooks or trace file ContextVar not visible in agent execution context
2. **Hooks not installed** - Something in the branch breaks hook installation timing
3. **Span never created** - Early failure before first span starts
4. **Export silently fails** - Exception swallowed in export() method

The fact that this only happens in later runs (run6+) suggests:
- State accumulation (file handles, memory)
- Resource exhaustion
- ContextVar corruption after many operations

## Related Issue

Some of these failures also show "No LLM available for AnalyzerSubAgent" errors, which is a separate but possibly related issue where `_parent_agent_var` ContextVar returns None.

---

## Analysis (2026-01-26)

### Branch Changes Review

The `history-phases-1-3` branch made NO changes to:
- `packages/openinference-instrumentation-nemo-oo-agents/` (tracing infrastructure)
- `src/nemo_oo_agents/runtime/hooks.py` (hook installation/invocation)

The branch DID change:
1. **Event API** - From `EventType(data=ContentData(content=...))` to `EventType(content=...)`
2. **BlockRenderer** - Simplified from `render()` + `render_async()` to just `render()` (async-only)
3. **Event formatting** - Changed from mutating events to returning `(event, formatted_content)` tuples
4. **History searches** - Changed from `event.data.content` to `event.content` access

### Key ContextVar Architecture

Three ContextVars are involved:

1. **`_instrumentation_hooks_var`** (`hooks.py:290-292`)
   - Stores the `OpenInferenceHooks` instance
   - Default: `None` - if not propagated, no spans created

2. **`_current_trace_file`** (`_jsonl_exporter.py:15`)
   - Per-sample trace file routing
   - Default: `None` - falls back to default trace file

3. **`_context_active_spans`** (`_hooks_impl.py:15-17`)
   - Tracks active spans for parent-child linking
   - Default: `None` with **lazy initialization** to `{}` (dict)

### Potential Root Cause: Shared Dict Reference

The `_context_active_spans` pattern is suspicious:

```python
_context_active_spans: ContextVar[dict[str, Span] | None] = ContextVar(
    "context_active_spans", default=None
)

def _get_active_spans() -> dict[str, Span]:
    spans = _context_active_spans.get()
    if spans is None:
        spans = {}
        _context_active_spans.set(spans)
    return spans
```

**Problem**: When `asyncio.gather()` runs concurrent tasks:
1. All tasks inherit parent's ContextVar state
2. If parent has `_context_active_spans = {}` (already initialized), ALL child tasks share the **same dict reference**
3. Race condition: Task A and Task B both modify the same spans dict
4. Span tracking becomes corrupted over many concurrent operations

This would explain why it manifests in "later runs" - the shared dict accumulates corruption over iterations.

### Why Trace Files Would Be Missing

If `_context_active_spans` is corrupted:
1. `before_generation()` creates a span and adds to dict
2. Race condition causes span to be overwritten/lost
3. `after_generation()` can't find the span, exits early
4. Span never ends → never exported → trace file empty

### Recommended Fix

Make `_context_active_spans` copy-on-write by creating a NEW dict for each async context:

```python
def _get_active_spans() -> dict[str, Span]:
    spans = _context_active_spans.get()
    if spans is None:
        spans = {}
        _context_active_spans.set(spans)
    else:
        # Check if we're in a new context that inherited parent's dict
        # If so, create our own copy
        # (Need to detect this somehow - e.g., via a marker or context ID)
        pass
    return spans
```

Or use `contextvars.copy_context()` when spawning concurrent tasks.

### Quick Verification Steps

1. **Add debug logging to `_jsonl_exporter.py:export()`:**
   ```python
   print(f"DEBUG export(): trace_file={get_trace_file()}, default={self.trace_file}")
   ```

2. **Add debug logging to hooks:**
   ```python
   # In call_before_hook
   print(f"DEBUG: get_hooks() = {get_hooks()}")
   ```

3. **Check if spans dict is shared:**
   ```python
   # In _get_active_spans()
   print(f"DEBUG: spans id={id(spans)}, len={len(spans)}")
   ```

### Alternative Hypothesis: Event Validation Failure

The event API changes could cause Pydantic validation errors if any code uses the old pattern:
- Old: `TaskEvent(data=ContentData(content="..."))`
- New: `TaskEvent(prompt="...")`

If validation fails early in `before_generation()`, the span might not be created. However, grep found no old-style patterns remaining.

### Next Steps

1. Run a minimal reproduction with debug logging enabled
2. Check if the issue reproduces on main branch (control test)
3. If branch-specific, bisect to find the breaking commit
4. If spans dict is the issue, implement copy-on-write fix

---

## Resolution (2026-01-26)

### Findings from Debug Session

Extensive instrumentation was added across the codebase to track execution flow:
- `AgentWrapper.run()` calls/returns/exceptions
- `metaclass.wrapper()` entry
- `CodeActStrategy.execute()` entry
- `before_agent_call` hook with span dict tracking
- Trace file ContextVar state

**Initial Observations:**
- First run (before instrumentation): 96 missing trace files
- Subsequent runs (with instrumentation): 0 missing trace files
- All ContextVars (`_instrumentation_hooks_var`, `_current_trace_file`) were propagating correctly
- Spans dict IDs were unique per sample (no shared dict issue detected)

### Hypothesis Tested and Rejected

Initial hypothesis: The unconditional `_get_active_spans()` call in `before_agent_call()` was the fix.

**Testing:**
1. Reverted `_hooks_impl.py` to original code pattern
2. Ran 192 samples with 4 runs, parallel=20
3. **Result: 0 missing trace files**

**Conclusion: Original hypothesis was WRONG**

The bug did not reproduce after reverting the suspected fix, which means:
1. The bug is **non-deterministic** (race condition that doesn't always trigger)
2. OR it was fixed by **other changes** made during instrumentation
3. OR it was already fixed in a **prior commit** on the branch

### Current Status: NOT REPRODUCIBLE

The missing trace files issue was observed once (96 samples) but could not be reproduced in subsequent runs despite multiple attempts with different configurations.

### Defensive Measures Retained

The following defensive patterns are recommended even though the root cause is unclear:

1. **Early spans dict initialization** in `_hooks_impl.py`:
   ```python
   spans_dict = _get_active_spans()  # Call unconditionally
   parent_span = spans_dict.get(parent_call_id) if parent_call_id else None
   ```

2. **Trace file verification** in `pipeline.py`:
   ```python
   set_trace_file(trace_file)
   actual_trace_file = get_trace_file()  # Verify immediately
   ```

### Test Created

Created `packages/openinference-instrumentation-nemo-oo-agents/tests/test_concurrent_spans.py` with tests for concurrent span tracking. However, these tests pass even with the "buggy" code, confirming the issue is subtle and timing-dependent.

### Recommendations

1. **Monitor for recurrence** - If the issue reappears, gather more data immediately
2. **Keep debug instrumentation** - The logging is low-overhead and helps diagnose future issues
3. **Accept uncertainty** - Some race conditions are difficult to reproduce deterministically
4. **Consider copy-on-write pattern** - For `_context_active_spans`, using `contextvars.copy_context()` when spawning async tasks would provide stronger isolation

### Files Modified

- `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_hooks_impl.py` - Debug logging (hypothesis tested)
- `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_jsonl_exporter.py` - Debug logging
- `src/nemo_oo_agents/runtime/hooks.py` - Debug logging
- `util/eval_pipeline/src/eval_pipeline/pipeline.py` - Debug logging and trace file verification
- `src/nemo_oo_agents/metaclass.py` - Debug logging
- `src/nemo_oo_agents/strategies/codeact.py` - Debug logging
- `util/eval_pipeline/src/eval_pipeline/agents.py` - Exception tracking

---

## Follow-up Analysis (2026-01-26 18:00)

### New Failure Pattern Discovered

While investigating further, a NEW error pattern emerged that correlates with missing traces:

```
[EXECUTE_ERROR] task=router_transform_001 error='NoneType' object is not callable
```

This error affects **all models simultaneously** at the same run number, suggesting global state pollution.

### Detailed Log Analysis

From `eval_debug_router_2_20260126_181726.log`:

```
Run 1-3: All models pass ✓
Run 4: ALL 6 models fail simultaneously with 'NoneType' object is not callable
  - gpt-oss-120b ✗ (0.1s)
  - qwen3-80b ✗ (0.1s)
  - claude-haiku ✗ (0.1s)
  - claude-sonnet ✗ (0.1s)
  - nemotron3-nano-30b ✗ (0.1s)
  - gemini-2.5-flash-lite ✗ (0.1s)
Run 5+: All models continue to fail
```

Total: 876 samples failed with this error (matches exactly the count of missing traces).

### Key Observations

1. **Simultaneous failure across all models** - Indicates global state, not per-model issue
2. **0.1s execution time** - Failure happens almost instantly
3. **No stack trace in log** - Exception is caught and only message logged
4. **`'NoneType' object is not callable`** - Something that should be callable became None

### Potential Causes for `'NoneType' object is not callable`

The error means code tried to call `something()` but `something` was `None`. Possible sources:

1. **LLM client became None** - But `llm_client.acall(...)` has explicit None check
2. **Method attribute became None** - `getattr(agent_instance, method_name)` returned None?
3. **Hook function became None** - Hooks ContextVar set to None?
4. **Factory function became None** - Closure variable corruption?

### Unit Test Attempts

Created `tests/capability/test_router_repeated_runs.py` with:
- `FakeLLM` that returns deterministic tool calls
- `RouterTestWrapper` testing with subagent creation
- Parallel execution with semaphore
- Tracing enabled

**Results:**
- 20 parallel runs: ALL PASSED
- linecache growth: 0->2 entries (stable)
- No reproduction of the bug

The bug only manifests in the full eval_pipeline context with real LLMs.

### Intermittent Nature

The bug does NOT reproduce consistently:
- Some runs: 0 missing traces
- Other runs: 366-876 missing traces
- Same code, same tests

This suggests a timing-dependent race condition that requires specific execution patterns to trigger.

### Next Steps

1. **Add full traceback logging** to `execute.py` when exceptions occur ✓ (done, see execute.py:63-65)
2. **Run with fewer parallel workers** to isolate the race condition
3. **Add stack dump on `'NoneType' object is not callable`** to identify exact call site
4. **Check closure variable capture** in `make_factory()` for potential issues ✓ (investigated, see Track D below)

---

## Track D Investigation: Closure Variable Capture (2026-01-26 18:30)

### Analysis of `make_factory()` Closure Pattern

The `make_factory()` function in `evaluator.py:376-390` uses a standard Python closure pattern with default parameter capture:

```python
if client_factory:
    def make_factory(cf=client_factory, cls=test.agent_class, method=test.method):
        def factory():
            fresh_client = cf()
            agent_instance = cls(llm=fresh_client)
            return AgentWrapper(agent_instance, method)
        return factory
else:
    def make_factory(c=client, cls=test.agent_class, method=test.method):
        def factory():
            agent_instance = cls(llm=c)
            return AgentWrapper(agent_instance, method)
        return factory
```

### Closure Correctness Assessment

**Pattern is CORRECT**: The default parameter pattern (`cf=client_factory`, `cls=test.agent_class`, etc.) is the standard Python idiom for capturing loop variables. Values are captured at function definition time, not at call time.

**Isolation is CORRECT**: Each call to `_create_sample()` creates its own local scope, so `make_factory` is defined fresh for each sample. No cross-contamination between samples.

**Call site is CORRECT**: `make_factory()` is called immediately after definition on line 403, returning the inner `factory` function.

### Potential Failure Points for `'NoneType' object is not callable`

The error could occur at these points:

1. **`sample.agent_factory()` returns None** - If `make_factory()` returned `None` instead of `factory`. But both branches explicitly `return factory`.

2. **`cf()` where `cf` is None** - The closure captures `cf=client_factory`. If `client_factory` was truthy at the `if` check but became falsy before capture, this could fail. However, `client_factory` is assigned from `self._model_factories.get(model_id)` immediately before the check, and the conditional definition happens atomically in the same stack frame.

3. **`cls()` where `cls` is None** - `cls` is captured from `test.agent_class`. If `EvalTest.agent_class` was mutated to `None` after sample creation but before execution. **No code was found that mutates `test.agent_class`.**

4. **`self.method()` in `AgentWrapper.run()`** - `self.method = getattr(agent_instance, method_name)`. If the agent instance doesn't have the method, `getattr` returns the result of `__getattr__` if defined, or raises `AttributeError`. **Agent class does not define `__getattr__` that returns None.**

### Key Observation: Simultaneous Failure Pattern

The log shows:
- Run 1-3: All models pass
- Run 4: ALL 6 models fail SIMULTANEOUSLY with 0.1s execution time
- Run 5+: ALL models continue to fail

This pattern indicates **global state corruption**, not per-sample closure issues. If the closure was incorrectly capturing a value, it would affect samples randomly based on when they were created, not all samples from a specific run onwards.

### Conclusion: Closure is NOT the Root Cause

The closure pattern is correct and cannot explain the simultaneous failure of all models. The bug is more likely caused by:

1. **ContextVar pollution** - A ContextVar is being set/unset incorrectly, affecting all tasks in the same context
2. **Global state mutation** - Some global state is modified during run 3's execution that breaks run 4+
3. **Timing-dependent race** - A race condition that only triggers under specific parallel execution patterns

### Recommendation

The closure pattern is sound. Focus investigation on:
1. `_instrumentation_hooks_var` - hooks becoming None
2. `_parent_agent_var` - parent agent becoming None (related to "No LLM available" errors)
3. Module-level state in agent classes or strategies
4. ContextVar inheritance in `asyncio.gather()` calls

---

## Track C Investigation: Class/Module State Mutation (2026-01-26 19:15)

### Summary

Investigation of potential class-level or module-level state corruption that could cause the simultaneous failure pattern where ALL models fail at the same run number.

### Hypothesis

The `'NoneType' object is not callable` error affecting all models simultaneously suggests **shared state pollution** at one of these levels:

1. **Class attribute mutation** - The `RouterTestWrapper` class object itself is modified
2. **Module attribute mutation** - A module-level binding becomes None
3. **Import mechanism corruption** - Dynamic imports return None
4. **Metaclass side effects** - The `AgentMeta` metaclass modifies class state during execution

### Investigation: make_factory() Closure Analysis

The `make_factory()` pattern in `evaluator.py` was analyzed:

```python
def make_factory(cf=client_factory, cls=test.agent_class, method=test.method):
    def factory():
        fresh_client = cf()
        agent_instance = cls(llm=fresh_client)
        return AgentWrapper(agent_instance, method)
    return factory
```

**Findings:**

1. **Default parameters are correct**: `cf`, `cls`, `method` are captured via default parameters, avoiding late-binding issues
2. **AgentWrapper capture**: `AgentWrapper` is imported at line 359 and captured as a free variable - this is CORRECT behavior
3. **Closure cells are not modified**: No code in the codebase modifies `__closure__` or `cell_contents`

**Conclusion**: The closure pattern itself is correct and shouldn't cause the issue.

### Investigation: Sample Creation vs Execution

Samples are created UPFRONT before execution:

```python
for run_id in range(1, runs + 1):
    for test in tests_to_run:
        for i, task in enumerate(tasks):
            for model_id in model_ids:
                sample = self._create_sample(...)
                all_samples.append(sample)
```

**Key insight**: All factory closures are created BEFORE any execution starts. If the issue was in factory creation, all samples would fail from run1.

The fact that run1-3 pass and run4+ fails indicates:
- **Corruption happens DURING execution**, not during sample creation
- The corruption affects ALL subsequent samples (shared state)
- 0.1s execution time means failure occurs before any LLM call

### Investigation: Class Attribute Access Path

When `factory()` is called at execution time:

1. `cls(llm=fresh_client)` - `cls` is `RouterTestWrapper` class
2. `RouterTestWrapper.__init__()` is called
3. Inside `__init__`, `super().__init__(**kwargs)` calls `Agent.__init__()`
4. `Agent.__init__()` resolves LLM via `_resolve_llm()`
5. `_resolve_llm()` accesses `_parent_agent_var` ContextVar

**Potential corruption point**: If `RouterTestWrapper.process` (the method attribute) becomes None after the class is created, `getattr(agent_instance, "process")` in AgentWrapper would return None.

### Investigation: Metaclass Method Replacement

The `AgentMeta.__new__()` method replaces ellipsis methods at class creation time:

```python
if should_generate or should_trace:
    wrapped = mcs._create_wrapper(...)
    setattr(cls, attr_name, wrapped)
```

**Finding**: `_create_wrapper()` always returns the wrapper function at the end:

```python
@staticmethod
def _create_wrapper(...) -> Callable:
    # ...
    @wraps(original_func)
    async def wrapper(self, *args, **kwargs):
        # ...
    wrapper._agent_decorator = "auto"
    wrapper._needs_generation = needs_generation
    wrapper._plan_strategy = strategy
    wrapper._original = original_func
    return wrapper  # Always returns wrapper
```

**Conclusion**: `_create_wrapper()` cannot return None unless an exception is raised before the return statement.

### Investigation: What Could Set Method to None?

Searched for any code that could set `process = None` or modify class attributes:

```bash
grep -r "setattr.*None" src/
grep -r "\.process\s*=" tests/
grep -r "__dict__\s*\[" src/
```

**Finding**: No code in the codebase sets method attributes to None.

### Remaining Hypotheses

1. **Memory corruption** (unlikely in CPython due to reference counting)
2. **Thread safety** in non-async code paths (eval_pipeline uses async, no threading found)
3. **GC collecting closures prematurely** (unlikely - closures hold strong refs)
4. **Import race conditions** (unlikely - imports happen at startup)

### Evidence Gaps

To definitively identify the root cause, we need:

1. **Full stack trace** when `'NoneType' object is not callable` occurs
2. **Closure inspection** at failure time: `print(factory.__closure__)`
3. **Class state dump**: `vars(RouterTestWrapper)` at failure time
4. **Object IDs tracking**: Track `id(cls)` across the entire execution

### Instrumentation Recommendations

Add to `factory()`:

```python
def factory():
    import sys
    # Debug dump closure state
    print(f"[FACTORY_DEBUG] cf={cf} type={type(cf)} id={id(cf)}", file=sys.stderr)
    print(f"[FACTORY_DEBUG] cls={cls} type={type(cls)} id={id(cls)}", file=sys.stderr)
    print(f"[FACTORY_DEBUG] AgentWrapper={AgentWrapper} id={id(AgentWrapper)}", file=sys.stderr)
    print(f"[FACTORY_DEBUG] method={method}", file=sys.stderr)

    if cf is None:
        raise TypeError("[TRACK_C] cf (client_factory) is None!")
    if cls is None:
        raise TypeError("[TRACK_C] cls (agent_class) is None!")
    if AgentWrapper is None:
        raise TypeError("[TRACK_C] AgentWrapper is None!")

    fresh_client = cf()
    agent_instance = cls(llm=fresh_client)
    return AgentWrapper(agent_instance, method)
```

### Conclusion

The closure variable capture pattern in `make_factory()` is **CORRECT** and should not cause the issue. The simultaneous failure across all models at run4 strongly suggests:

1. **Runtime state corruption** that affects all concurrent samples
2. **ContextVar pollution** across async tasks
3. **Something external to the factory pattern** causing the failure

The most productive next step is to **reproduce the issue with full traceback logging** and **closure state inspection** to identify the exact point of failure.

### Status: INVESTIGATION COMPLETE - Root Cause Unclear

The Track C investigation confirms the closure pattern is correct but could not identify the exact corruption mechanism. The issue remains intermittent and the root cause is still unknown.

---

## Track A Investigation (2026-01-26 19:30)

### Summary

Detailed analysis of the `'NoneType' object is not callable` error pattern.

### Key Observations

1. **Timing Pattern**: Error starts at sample 66 (~53.2s), affecting ALL subsequent samples
   - Sample 65 (run3): passes at 53.1s
   - Sample 66 (run4): fails at 53.2s - first failure
   - All subsequent samples fail (0.1s execution time)

2. **All Models Fail Simultaneously**: gpt-oss-120b, qwen3-80b, claude-haiku, claude-sonnet, nemotron3-nano-30b, gemini-2.5-flash-lite all fail at the same "run number" (run4+)

3. **No Stack Trace in Logs**: The log file (`eval_debug_router_2_20260126_181726.log`) shows `[EXECUTE_ERROR]` but no stack trace, suggesting the `traceback.print_exc()` code was added AFTER this run or stderr wasn't captured

4. **Fast Execution Time**: All failures show 0.1s latency, indicating the error occurs very early (before any LLM call)

### Error Location Analysis

The error `'NoneType' object is not callable` occurs when calling `something()` where `something` is `None`. Potential sources:

1. **AgentWrapper.run()**: `await self.method(*args, **kwargs)` - if `self.method` is None
2. **Factory closure**: `cf()` in `make_factory` - if `cf` (client_factory) becomes None
3. **Hook calls**: `getattr(hooks, hook_name)()` - if hook method is None
4. **Strategy execution**: Various callable lookups in metaclass/actor

### Instrumentation Added

**util/eval_pipeline/src/eval_pipeline/agents.py** - Added defensive checks:
```python
# In AgentWrapper.__init__:
if self.method is None:
    raise TypeError(f"Method '{method_name}' is None on {type(agent_instance).__name__}")

# In AgentWrapper.run():
if self.method is None:
    raise TypeError(f"[TRACK_A_BUG] Method '{self.method_name}' became None after construction!")
```

### Hypothesis Refinement

The simultaneous failure across all models suggests **global state pollution** rather than per-sample issues:

1. **Class-level mutation**: Something modifies the `RouterTestWrapper` class object, affecting all future instantiations
2. **ContextVar corruption**: A ContextVar gets corrupted and affects all concurrent tasks
3. **Module-level state**: Something in a shared module becomes None

### Missing Evidence

Need to capture:
1. **Full stack trace** at the exact point of failure
2. **Class object state** at failure time (e.g., `vars(RouterTestWrapper)`)
3. **Factory closure values** at definition vs execution time

### Next Reproduction Steps

1. Run with stderr captured to file (not just terminal)
2. Add `print(f"cf={cf}, cls={cls}, method={method}", file=sys.stderr)` in factory()
3. Run with `parallel=1` to eliminate race conditions
4. Add validation in `AgentWrapper.__init__` to catch early (DONE)

### Files Modified

- `util/eval_pipeline/src/eval_pipeline/agents.py` - Defensive checks for None method in AgentWrapper
- `util/eval_pipeline/src/eval_pipeline/evaluator.py` - Checks for method existence on class before instantiation
- `src/nemo_oo_agents/metaclass.py` - Validation that _create_wrapper never returns None

### Instrumentation Summary

The following defensive checks have been added to catch the bug next time:

1. **AgentWrapper.__init__** - Validates `self.method` is not None immediately after getattr
2. **AgentWrapper.run()** - Re-validates `self.method` is not None (detect post-construction corruption)
3. **Factory functions** - Check `getattr(cls, method, None)` before agent instantiation
4. **Metaclass** - Validate wrapper function is not None before setattr

### Recommended Reproduction Steps

To reproduce the bug with instrumentation active:

```bash
cd util/eval_pipeline
source ../../.venv/bin/activate

# Run with stderr captured to file
python -m eval_pipeline run \
    --config tests/capability.yaml \
    --tests router_validate,router_transform \
    --runs 40 \
    --parallel 20 \
    2>&1 | tee eval_debug_$(date +%Y%m%d_%H%M%S).log
```

When the bug occurs, look for:
- `[TRACK_A_BUG]` - Method became None on class
- `[FACTORY_ERROR]` - Closure variable corruption
- `[WRAPPER_ERROR]` - Method None in AgentWrapper
- `[METACLASS_BUG]` - Wrapper creation failure

### Remaining Investigation Areas

1. **Memory pressure**: Does the bug correlate with memory usage?
2. **Thread safety**: Is there any non-async code modifying shared state?
3. **Garbage collection**: Are weak references involved?
4. **Import side effects**: Does re-importing modules cause issues?

---

## Track B Investigation (2026-01-26 19:30)

### Summary

Track B focuses on the `'NoneType' object is not callable` error pattern discovered during the initial investigation.

### Key Observations

1. **Simultaneous failure**: ALL models fail at the same run number (run 4+)
2. **Very fast failure**: 0.1s execution time indicates failure during initialization, not LLM calls
3. **876 affected samples**: Matches exactly the missing trace count
4. **Global state corruption**: The simultaneous cross-model failure suggests shared state being polluted

### Possible Failure Points for "'NoneType' object is not callable"

Based on code analysis, the error could occur at:

1. **`AgentWrapper.__init__`**: `self.method = getattr(agent_instance, method_name)` returns None
2. **`AgentWrapper.run`**: `await self.method(*args, **kwargs)` where `self.method` became None
3. **Factory closure**: `cls(llm=c)` where `cls` is None
4. **Hooks**: `getattr(hooks, hook_name)(**kwargs)` where the hook attribute is None

### Defensive Measures Added

The following defensive logging was added to catch the exact failure point:

#### 1. AgentWrapper (`agents.py`)
- Validates method exists and is callable at construction time
- Re-validates method is still callable in `run()` before calling
- Prints diagnostic info including available methods if validation fails

#### 2. Factory Creation (`evaluator.py`)
- Validates `cls` (agent_class) is not None and is callable
- Validates `c` (client) or `cf` (client_factory) is not None
- Prints closure variable state if validation fails

#### 3. Pipeline (`pipeline.py`)
- Validates `agent_factory` is not None and is callable
- Catches and logs exceptions from `agent_factory()` with full traceback
- Validates `agent_factory()` return value is not None

### Analysis: Why All Models Fail Simultaneously

The simultaneous failure across all models suggests ONE of these scenarios:

1. **Global module/class corruption**: Something modifies a shared class attribute
2. **ContextVar race condition**: A ContextVar value gets incorrectly propagated
3. **Resource exhaustion**: File handles, memory, or other resources depleted
4. **Tracer provider corruption**: OTel tracer or hooks become invalid

The fact that runs 1-3 pass and run 4+ all fail suggests:
- State accumulates across runs
- A threshold is crossed at run 4
- The corruption affects all subsequent samples regardless of model

### Next Steps

1. **Run evaluation with defensive logging enabled** to capture exact failure point
2. **Check if linecache accumulation correlates** with failure threshold
3. **Monitor ContextVar state** across runs for unexpected changes
4. **Add memory/resource monitoring** to detect exhaustion patterns

### Files Modified

- `util/eval_pipeline/src/eval_pipeline/agents.py` - Defensive validation in AgentWrapper
- `util/eval_pipeline/src/eval_pipeline/evaluator.py` - Factory closure validation
- `util/eval_pipeline/src/eval_pipeline/pipeline.py` - agent_factory validation

---

## ROOT CAUSE FOUND (Jan 27, 2026)

**The LLM replaces the class method `RouterTestWrapper.process` with a factory-created function that runs OUTSIDE of `execute_python` context.**

### Evidence from Logs

From `eval_debug_router_1_20260126_185755.log`, Cell In[8]:

```python
def _make_process_method():
    async def process(self, user_message: str, values: list[float]):
        # ... creates sub-agents without llm=self._llm ...
        validator = self.ValidatorSubAgent()  # CRASHES HERE
        coros['Validator'] = validator.validate(values)
        # ...
    return process

# CRITICAL BUG: Attaches to the CLASS, not the instance!
RouterTestWrapper.process = _make_process_method()
```

### Why This Causes the Bug

1. **Factory pattern breaks context**: The `process` function returned by `_make_process_method()` is defined OUTSIDE of `execute_python`, so when it runs later, `_parent_agent_var` is not set.

2. **Class-level mutation persists**: Once one sample's LLM replaces `RouterTestWrapper.process`, ALL subsequent samples use this contaminated version.

3. **Sub-agent instantiation fails**: When the replaced method creates `self.ValidatorSubAgent()`, it looks for `_parent_agent_var.get()` which returns `None` (since we're not inside `execute_python`).

### Stack Trace Confirmation

```
File "Cell In[8]", line 32, in process
    validator = self.ValidatorSubAgent()
File "/Volumes/dev/dev/nemo_oo_agents/src/nemo_oo_agents/agent.py", line 222, in __init__
    self._llm: UnifiedLLM = self._resolve_llm(instance_llm)
...
CRASH: No LLM available for ValidatorSubAgent
_parent_agent_var.get() returned: None
```

### Fix Options

#### Option A: AST Validator Enhancement (Recommended)
Add check to `PlanningLanguageValidator` to forbid class attribute assignment:
- Detect pattern: `ClassName.attr = ...` (Attribute on Name target)
- Reject code that modifies class-level attributes
- **Pros**: Prevents the bug at code validation time, clear error message
- **Cons**: May be overly restrictive for legitimate use cases

#### Option B: Prompt Engineering
Update system prompt to explicitly:
- Forbid `ClassName.method = factory()` patterns
- Require `llm=self._llm` for all sub-agent instantiation
- **Pros**: No code changes, works immediately
- **Cons**: LLMs may still violate the constraint

#### Option C: Runtime Protection
Make Agent method attributes read-only using `__setattr__` override or descriptors:
- Raise error if generated code tries to modify class methods
- **Pros**: Catches the bug at runtime with clear error
- **Cons**: More complex, may have performance impact

#### Option D: Defensive Sub-Agent Instantiation
Modify `Agent.__init__` to fall back to `self._llm` when `_parent_agent_var` is None but caller has access to parent:
- Check if `self` is bound to an instance with `_llm` available via stack inspection
- **Pros**: Makes the failing pattern work
- **Cons**: Complex, may hide other bugs

### Recommended Fix: Option A

The AST validator approach is cleanest because:
1. Catches the bug early (before execution)
2. Provides clear error message to the LLM
3. Is consistent with existing validation (forbidden patterns)
4. Has no runtime overhead

Implementation: Add to `validator.py`:
```python
def _check_class_attribute_assignment(self, node: ast.Assign):
    """Forbid ClassName.attr = value patterns."""
    for target in node.targets:
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            # This is: SomeName.attr = ...
            raise ValidationError(
                f"Class attribute assignment forbidden: {target.value.id}.{target.attr}"
            )
```

### Test Coverage

Unit tests created in `tests/capability/test_class_method_replacement_bug.py`:
- `test_factory_method_outside_execute_python_has_no_context` - Reproduces exact bug
- `test_class_method_replacement_breaks_context` - E2E confirmation
- `test_detect_class_attribute_assignment` - Placeholder for validator fix
