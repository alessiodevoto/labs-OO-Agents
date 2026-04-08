# Unified Runtime Simplification - Refactor Assessment

**Date**: 2025-12-04
**Branch**: `feat/unified-runtime-simplification`
**Last Updated**: 2025-12-04 (final cleanup complete)

---

## Executive Summary

**Phase 1 (Hook-Based Instrumentation)**: SKIPPED for now
**Phase 2 (Strategy Middleware)**: ✅ COMPLETE
**Phase 3 (Context Blocks)**: NOT STARTED - will implement in place (not extract to separate package)

---

## Completed Cleanup (2025-12-04)

### 1. Added `requires_lock` Property ✅

Added to `GenerationStrategy` ABC with default `True`:
```python
@property
def requires_lock(self) -> bool:
    """Whether this strategy needs exclusive generation access."""
    return True
```

Updated `ActorRuntime` to check `strategy.requires_lock` before acquiring lock.

### 2. Removed Dead Code from types.py ✅

| Code | Status |
|------|--------|
| `EventType` enum (55 lines) | **Removed** |
| `ExecutionConfig` class (29 lines) | **Removed** |
| `GenerationStrategy` enum | **Removed** |

`types.py` now contains only `ContextEntry`.

### 3. Updated Public API ✅

- `agent006.GenerationStrategy` now exports the ABC (from strategies)
- `agent006.PurePythonStrategy` exported for direct use
- Removed `PURE_PYTHON`, `STRUCTURED_OUTPUT` convenience aliases
- `@plan(strategy="PURE_PYTHON")` string shorthand still works

### 4. External Files with Dead Imports (Low Priority)

| File | Dead Import | Status |
|------|-------------|--------|
| `examples/tracing.py` | `agent006.tracing.otel` | Pending |
| `examples/memory_trace_example.py` | `agent006.tracing.otel` | Pending |
| `agents/tpm-agent/runner.py` | `agent006.tracing.otel` | Pending |

---

## Phase 2: Strategy Middleware & Runtime Refactor

### Completed Components ✅

#### 1. Event Types (`src/agent006/events.py`)

```
✅ EventBase (id, timestamp, metadata) - id is auto-generated UUID
✅ ContentData (content wrapper)
✅ TaskEvent, MessageEvent, ReasoningEvent, ErrorEvent, FeedbackEvent, AssistantEvent
✅ Event discriminated union
✅ ExecutionResult (stdout, error, defined_methods)
```

**Matches design spec exactly.**

#### 2. HistoryManager (`src/agent006/runtime/history.py`)

```
✅ add(event, record=True) -> str - returns event.id
✅ on(event_type, handler) - subscription with unsubscribe
✅ recent(limit) - bounded retrieval
✅ for_call(call_id) - call-scoped filtering
✅ for_call_tree(call_id) - nested call filtering
✅ since(event_id) - uses event.id for cursor-based retrieval
```

**Matches design spec exactly.**

#### 3. GenerationStrategy ABC (`src/agent006/strategies/base.py`)

```
✅ name property (abstract)
✅ strategy_prompt property (abstract)
✅ requires_lock property (default True)
✅ execute(runtime, call) abstract method
✅ RuntimeServices protocol defined
```

**Matches design spec exactly.**

#### 4. CurrentCall Dataclass (`src/agent006/strategies/current_call.py`)

```
✅ id, method_name, decorator, signature, docstring
✅ args, kwargs, parent_id
```

**Matches design spec.**

#### 5. PurePythonStrategy (`src/agent006/strategies/pure_python.py`)

```
✅ Configurable max_iterations, max_retries
✅ Uses Event types correctly (TaskEvent, ErrorEvent, FeedbackEvent, etc.)
✅ Provides reasoning() and message() builtins
✅ Proper error handling and retry logic
```

**Matches design spec.**

#### 6. RuntimeServices in ActorRuntime (`src/agent006/runtime/actor.py`)

```
✅ agent property
✅ history property (returns HistoryManager)
✅ generate() method - builds messages from context if not provided
✅ execute_code() method - with builtins support
```

**Note**: `execute_nested()` removed - nested @plan calls work implicitly via `call_plan()` + `_in_generation_session`.

**Core implementation complete.**

---

### Phase 2 Completion Log (2025-12-04)

All Phase 2 tasks completed:

1. ✅ **RuntimeServices.generate()** - Removed `messages` param, runtime builds from context
2. ✅ **RuntimeServices.execute_code()** - Removed `extra_globals`, merged into `builtins`
3. ✅ **PurePythonStrategy** - Deleted `_build_messages()`, calls `runtime.generate()` directly
4. ✅ **Context variables** - Fixed parallel nested calls by using contextvars for `_current_call`, `_current_method`, `_current_strategy`
5. ✅ **Deleted legacy code**:
   - `src/agent006/runtime/executors/` (~2000 lines)
   - `src/agent006/runtime/errors/messages/` (~300 lines)
6. ✅ **Tests** - 443 passed, 92 skipped (old executor tests)
7. ✅ **Strategy simplification** - Removed `_execute_python_code()` wrapper:
   - Replaced with simple `_build_builtins()` method
   - Strategy now calls `runtime.execute_code()` directly
   - Removed `_defined_methods` instance variable
   - Works directly with `ExecutionResult` from runtime
8. ✅ **Removed `execute_nested()`** - Redundant, nested @plan calls work implicitly:
   - `call_plan()` + `_in_generation_session` handles lock inheritance
   - Generated code just calls `await self.other_method()` directly
   - Design doc's composite strategy version (`execute_strategy(strategy, call)`) can be added later if needed
9. ✅ **Event.id** - Added auto-generated UUID to EventBase:
   - `EventBase.id` field with `default_factory=lambda: str(uuid.uuid4())`
   - `HistoryManager.add()` now returns `event.id`
   - `since(event_id)` uses `event.id` instead of `event.metadata.get("event_id")`

---

## Phase 3: Context Blocks (In-Place)

### Current State: NOT STARTED

**Goal**: Implement context-blocks design improvements in place within agent006 (not extract to separate package).

### Files to Refactor

| Current Location | LOC | Description |
|-----------------|-----|-------------|
| `context/renderer.py` | 306 | BlockRenderer class |
| `context/formats.py` | 139 | Formatters (no base class) |
| `context/scoped.py` | 118 | ScopedContext manager |
| `util/context_blocks.py` | 202 | Block manipulation helpers |
| `util/prompt.py` | 115 | Utility functions |
| **Total** | ~880 | |

### Key Design Changes

1. **Pydantic Block models** with `show` expression for conditional visibility
2. **Typed Event model** with discriminated unions (already done in events.py)
3. **Two orthogonal formatters**:
   - `BlockFormatter` (XML, Markdown) - how to format blocks
   - `ProviderFormatter` (OpenAI, Anthropic) - how to assemble for provider
4. **OO BlockManager** replacing C-style functions
5. **Pluggable eval** - `render(spec, eval=..., ...)` takes callable per-render

---

## Implementation Plan

### Phase 2: ✅ COMPLETE (2025-12-04)

All tasks completed - see Phase 2 Completion Log above.

### Phase 3: Context Blocks (In-Place)

| Step | Task | Notes |
|------|------|-------|
| 3.1 | Add Pydantic Block models | `context/models.py` - Block with `show`, BlockSection, ContextSpec |
| 3.2 | Add BlockFormatter ABC | `context/formatter.py` - XML, Markdown implementations |
| 3.3 | Add ProviderFormatter ABC | `context/formatter.py` - OpenAI, Anthropic implementations |
| 3.4 | Add OO BlockManager | `context/manager.py` - Replace C-style functions |
| 3.5 | Update BlockRenderer | `context/renderer.py` - Use new models + formatters |
| 3.6 | Update ScopedContext | `context/scoped.py` - Use new Block models |
| 3.7 | Migrate existing code | Update imports throughout agent006 |
| 3.8 | Delete old utilities | `util/context_blocks.py`, `util/prompt.py` |

---

## Next Steps

1. **Phase 3**: Implement Pydantic Block models with `show` expression
2. **Phase 3**: Add BlockFormatter and ProviderFormatter ABCs
3. **Phase 3**: Refactor BlockRenderer to use new architecture
4. **Phase 3**: Update agent006 to use new context block system
