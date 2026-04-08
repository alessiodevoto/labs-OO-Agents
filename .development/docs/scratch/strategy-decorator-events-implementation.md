# Strategy Decorator Events Implementation

**Date**: 2026-02-13
**Status**: ✅ Implemented

## Final Design

The implementation uses a unified `ScopedContext` class that works both as:
1. **Context Manager**: `with ScopedContext(context={...}, events={...}):`
2. **Decorator Parameter**: `@strategy(CodeActStrategy(), ScopedContext(events={...}))`

This unified syntax provides consistency while supporting both use cases:
- Methods with bodies can use `with` statements
- Ellipsis methods (which have no body) can pass `ScopedContext` to the decorator

The `ScopedContext` class follows the same naming pattern as `DynamicContext`, enhancing API consistency.

## Implementation Status

### ✅ Implemented: Scoped Block Events
- `ScopedContext(events={...})` fully working
- Events injected as USER-role message blocks
- Phase 8 in context builder pipeline

### ✅ Implemented: Strategy Decorator Events
- `@strategy(ScopedContext(events={...}))` fully implemented
- Decorated methods can inject event-like messages
- Phase 7 (after Events, before Scoped Events)

## What Was Implemented

The actual implementation differs from the original plan below. Instead of adding separate `events=` and `context=` parameters, we created a unified `ScopedContext` class that can be used in both decorator and context manager contexts.

### Key Changes

**File**: `src/nemo_oo_agents/decorators.py`

Added `scoped_context` parameter that accepts a `ScopedContext` instance:

```python
def strategy(
    strategy_instance: GenerationStrategyABC | None = None,
    scoped_context: ScopedContext | None = None,  # NEW unified approach
    *,
    llm: UnifiedLLM | None = None,
    context: dict | None = None,  # Deprecated, kept for backward compatibility
) -> Callable[[Callable[P, R]], Callable[P, R]]:
```

The decorator extracts context and events from the `ScopedContext` instance:
```python
if scoped_context is not None:
    final_context = scoped_context.context
    final_events = scoped_context.events
func._strategy_context = final_context
func._strategy_events = final_events
```

**File**: `packages/context-blocks/src/context_blocks/scoped.py`

Refactored from `@contextmanager` function to class:

```python
class ScopedContext:
    def __init__(self, context=None, events=None):
        self.context = context
        self.events = events

    def __enter__(self): ...
    def __exit__(self, *args): ...
```

## Original Implementation Plan

*(Note: The sections below describe the original plan. The actual implementation uses the unified ScopedContext approach described above.)*

### 1. Update `@strategy` Decorator Signature

**File**: `src/nemo_oo_agents/decorators.py`

Add `events=` parameter:

```python
def strategy(
    strategy_instance: GenerationStrategyABC | None = None,
    *,
    llm: UnifiedLLM | None = None,
    context: dict | None = None,
    events: dict | None = None,  # NEW
) -> Callable[[Callable[P, R]], Callable[P, R]]:
```

Store on function:
```python
func._strategy_context = context
func._strategy_events = events  # NEW
```

### 2. Add Contextvar for Decorator Events

**File**: `src/nemo_oo_agents/runtime/actor.py`

Add new contextvar after `_decorator_context_var`:

```python
_decorator_events_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "decorator_events", default=None
)
```

### 3. Merge and Propagate Decorator Events

**File**: `src/nemo_oo_agents/runtime/actor.py` (in `_execute_with_generation`)

Around line 1725, add event merging alongside context merging:

```python
# Propagate decorator context to nested calls
parent_ctx = _decorator_context_var.get()
own_ctx = getattr(getattr(method, "__func__", method), "_strategy_context", None)
merged_ctx: dict[str, Any] | None = None
if parent_ctx or own_ctx:
    merged_ctx = {}
    if parent_ctx:
        merged_ctx.update(parent_ctx)
    if own_ctx:
        merged_ctx.update(own_ctx)
decorator_ctx_token = _decorator_context_var.set(merged_ctx)

# NEW: Propagate decorator events to nested calls
parent_evt = _decorator_events_var.get()
own_evt = getattr(getattr(method, "__func__", method), "_strategy_events", None)
merged_evt: dict[str, Any] | None = None
if parent_evt or own_evt:
    merged_evt = {}
    if parent_evt:
        merged_evt.update(parent_evt)
    if own_evt:
        merged_evt.update(own_evt)
decorator_evt_token = _decorator_events_var.set(merged_evt)
```

And reset in finally block:
```python
_decorator_context_var.reset(decorator_ctx_token)
_decorator_events_var.reset(decorator_evt_token)  # NEW
```

### 4. Pass to build_context()

**File**: `src/nemo_oo_agents/runtime/actor.py` (in `_prepare_context`)

Around line 1986, add parameter:

```python
result = await build_context(
    context_manager=self.agent.context_manager,
    event_manager=self.agent.event_manager,
    strategy=strategy,
    resolve_fn=_resolve_value,
    decorator_context=_decorator_context_var.get(),
    scoped_context=_scoped_blocks_var.get(),
    scoped_events=_scoped_events_var.get(),
    decorator_events=_decorator_events_var.get(),  # NEW
)
```

### 5. Add build_context() Parameter

**File**: `src/nemo_oo_agents/runtime/context_builder.py`

Add parameter to `build_context()`:

```python
async def build_context(
    *,
    context_manager: ContextManager,
    event_manager: Any,
    strategy: GenerationStrategy | None,
    resolve_fn: ResolveFunc,
    decorator_context: dict[str, Any] | None = None,
    scoped_context: dict[str, Any] | None = None,
    scoped_events: dict[str, Any] | None = None,
    decorator_events: dict[str, Any] | None = None,  # NEW
) -> BuildResult:
```

Update docstring:
```python
    decorator_events: Merged @strategy(events={...}) overrides from the
        current method and its parents. Passed explicitly by the actor
        instead of being read from a context variable.
```

### 6. Add Phase for Decorator Events

**File**: `src/nemo_oo_agents/runtime/context_builder.py`

Add new phase AFTER _phase_events but BEFORE _phase_scoped_events:

```python
# --- Events ---
blocks = _phase_events(blocks, event_manager)

# --- Decorator events (@strategy(events={...})) ---
blocks = await _phase_decorator_events(blocks, decorator_events, resolve_fn)  # NEW

# --- Scoped events (temporary message blocks) ---
blocks = await _phase_scoped_events(blocks, scoped_events, resolve_fn)
```

Implement the phase function (can reuse logic from _phase_scoped_events):

```python
async def _phase_decorator_events(
    blocks: list[ResolvedBlock],
    decorator_events: dict[str, Any] | None,
    resolve_fn: ResolveFunc,
) -> list[ResolvedBlock]:
    """Inject event-like message blocks from @strategy(events={...}).

    These blocks are added as USER-role messages after the real events,
    providing a way to inject instructions or reminders at the method level.

    Unlike scoped events (which are temporary within a with block),
    decorator events persist across all LLM calls within the method
    and its nested calls (unless overridden by nested decorators).

    Args:
        blocks: Current block list (not mutated).
        decorator_events: Dict of key -> value for decorator event blocks.
            - str: Static content
            - DynamicContext("expr"): DynamicContext expression
            - None: Skipped (no-op)
        resolve_fn: Async function to resolve DynamicContext values.
    """
    if not decorator_events:
        return blocks

    new_blocks: list[ResolvedBlock] = []
    for key, value in decorator_events.items():
        if value is None:
            continue

        content = await resolve_fn(key, value)
        if content is None:
            content = "None"

        if isinstance(value, DynamicContext):
            meta = BlockMetadata(expr=value.expr)
        else:
            meta = BlockMetadata(expr=f'@strategy.events["{key}"]')

        new_blocks.append(
            ResolvedBlock(
                key=f"decorator_event_{key}",
                content=content,
                role=Role.USER,
                metadata=meta,
            )
        )

    return [*blocks, *new_blocks]
```

## Updated Pipeline Order

After implementation, the 8-phase pipeline will be:

1. Framework blocks (system_prompt, self-doc, context_api, events_api)
2. Persistent blocks (self.context)
3. Strategy block overrides (strategy.get_block_overrides())
4. Decorator context (@strategy(ScopedContext(context={...})))
5. Scoped context blocks (with ScopedContext(context={...}))
6. Events (real conversation history)
7. **Decorator events (@strategy(ScopedContext(events={...})))** ← NEW
8. Scoped events (with ScopedContext(events={...}))

## Usage Examples

### Example 1: Static Reminder Messages

```python
class MyAgent(Agent):
    @strategy(
        CodeActStrategy(),
        context={"focus": "Write clean, tested code"},
        events={"reminder": "Remember to add docstrings and type hints"}
    )
    async def implement_feature(self, spec: str):
        ...
```

The "reminder" message will appear as a USER-role message after the real events, visible to the LLM on every turn within this method.

### Example 2: Method-Scoped Event History (Dynamic)

This powerful pattern lets a method show only its own execution history to the LLM, filtering out events from parent or sibling methods:

```python
class MyAgent(Agent):
    @strategy(
        ReflexionStrategy(max_reflections=3),
        events={
            "method_history": DynamicContext(
                "self.runtime.event_manager.filter(call_id=self.runtime.current_call.call_id)"
            )
        }
    )
    async def solve_with_reflection(self, problem: str):
        """
        The LLM sees only events from THIS method's execution,
        not events from parent methods or other branches.
        This prevents confusion from interleaved execution contexts.
        """
        ...
```

When the method makes multiple LLM calls (e.g., during reflection iterations), each call sees only the events from this specific method invocation, making the context cleaner and more focused.

## Testing Requirements

Add tests in `tests/runtime/test_context_builder.py`:

1. `test_decorator_events_basic` - Basic decorator events injection
2. `test_decorator_events_merge_with_parent` - Parent method events inherited by child
3. `test_decorator_events_override_parent` - Child can override parent events with same key
4. `test_decorator_events_with_dynamic` - DynamicContext values in decorator events
5. `test_scoped_events_override_decorator_events` - Scoped (Phase 8) overrides decorator (Phase 7)
6. `test_decorator_events_method_scoped_history` - **NEW: Test call_id filtering use case**
   - Create method with `events={"history": DynamicContext("self.runtime.event_manager.filter(call_id=self.runtime.current_call.call_id)")}`
   - Verify that only events from this method's call_id appear in the decorator event block
   - Verify parent method events are excluded
7. Full integration test with both decorator and scoped events

## Implementation Considerations

### Runtime Access in DynamicContext Expressions

✅ **RESOLVED**: DynamicContext expressions have access to:
- `self.runtime.current_call` - The CurrentCall object with call_id (public property added)
- `self.runtime.event_manager` - The EventManager with filter() method

The public `current_call` property was added to ActorRuntime to enable method-scoped event filtering.

## Open Questions

1. Should decorator events support removal (None) like context blocks do?
   - **Recommendation**: Yes, for consistency and to allow child methods to remove parent's events

2. What role should decorator events use?
   - **Recommendation**: USER role (like scoped events) since they're instructions/reminders

3. Should we allow ASSISTANT role events too?
   - **Recommendation**: Not in initial implementation. Can add later if needed.

4. ✅ Is `self.runtime.current_call.call_id` accessible in DynamicContext expressions?
   - **RESOLVED**: Added public `current_call` property to ActorRuntime
