# Plan: Trace All Async Methods When _enable_tracing = True

## Current Behavior

When a class sets `_enable_tracing = True`:
- ❌ Only public ellipsis methods are traced
- ❌ Private methods (starting with `_`) are never traced
- ❌ Regular Python methods (non-ellipsis) are never traced
- ✅ `@no_trace` decorator can opt-out public ellipsis methods

## Target Behavior

When a class sets `_enable_tracing = True`:
- ✅ **ALL async methods are traced** (public, private, dunder)
- ✅ **Both ellipsis AND regular Python methods are traced**
- ✅ `@no_trace` decorator can opt-out any method

## Implementation Changes

### File: `src/nemo_oo_agents/metaclass.py`

#### Change 1: Update wrapping condition (lines 164-168)

**Current:**
```python
# Only wrap if needs generation (ellipsis body)
if should_generate:
    strategy = mcs._resolve_strategy(attr_value)
    wrapped = mcs._create_wrapper(attr_value, should_generate, should_trace, strategy)
    setattr(cls, attr_name, wrapped)
```

**New:**
```python
# Wrap if needs generation OR tracing
if should_generate or should_trace:
    strategy = mcs._resolve_strategy(attr_value)
    wrapped = mcs._create_wrapper(attr_value, should_generate, should_trace, strategy)
    setattr(cls, attr_name, wrapped)
```

**Why:** This allows non-ellipsis methods to be wrapped for tracing purposes.

---

#### Change 2: Update _should_trace to allow all methods (lines 186-206)

**Current:**
```python
@staticmethod
def _should_trace(method_name: str, method_obj: Callable, should_trace_class: bool) -> bool:
    """Check if method should be traced."""
    if not should_trace_class:
        return False  # Class doesn't enable tracing

    if method_name.startswith("_"):
        return False  # Never trace private/dunder

    if getattr(method_obj, "_no_trace", False):
        return False  # Explicit opt-out

    return True
```

**New:**
```python
@staticmethod
def _should_trace(method_name: str, method_obj: Callable, should_trace_class: bool) -> bool:
    """Check if method should be traced.

    When should_trace_class is True, all async methods are traced
    (public, private, dunder) unless explicitly opted-out with @no_trace.
    """
    if not should_trace_class:
        return False  # Class doesn't enable tracing

    if getattr(method_obj, "_no_trace", False):
        return False  # Explicit opt-out

    return True
```

**Why:** Remove the private method filter - trace everything unless opted-out.

---

#### Change 3: Update _create_wrapper to handle tracing-only methods (lines 264-370)

The wrapper currently assumes all wrapped methods need generation. We need to add a conditional path for tracing-only methods.

**Current structure:**
```python
async def wrapper(self, *args, **kwargs):
    # Always validate arguments
    ArgumentValidator().validate(original_func, args, kwargs)

    # Always resolve strategy
    resolved_strategy = strategy or get_default_strategy()

    # Always route through runtime._call_plan()
    if hasattr(self, "runtime"):
        # ... hooks ...
        result = await self.runtime._call_plan(wrapper, args, kwargs)
        # ... hooks ...
    else:
        # ... strategy methods ...
```

**New structure:**
```python
async def wrapper(self, *args, **kwargs):
    # Only validate for generation methods
    if needs_generation:
        ArgumentValidator().validate(original_func, args, kwargs)

    # Only resolve strategy for generation methods
    if needs_generation:
        resolved_strategy = strategy or get_default_strategy()
    else:
        resolved_strategy = None

    if hasattr(self, "runtime"):
        # --- Tracing hooks (always) ---
        call_id = str(uuid4())
        parent_call_id = runtime._agent_call_id

        hook_context = call_before_hook(
            "before_agent_call",
            agent=self,
            method_name=original_func.__name__,
            args=args,
            kwargs=kwargs,
            call_id=call_id,
            parent_call_id=parent_call_id,
            **(strategy_config if needs_generation else {})
        )

        _push_agent_call_id(call_id)

        try:
            if needs_generation:
                # Route through runtime._call_plan for LLM generation
                result = await self.runtime._call_plan(wrapper, args, kwargs)
            else:
                # Call original function directly (tracing-only)
                result = await original_func(self, *args, **kwargs)
            return result
        except Exception as e:
            exception_caught = e
            raise
        finally:
            _pop_agent_call_id()
            call_after_hook("after_agent_call", hook_context, ...)
    else:
        # ... strategy methods (unchanged) ...
```

**Why:**
- Tracing-only methods don't need argument validation or strategy resolution
- They should call the original function directly, not through `_call_plan` (which handles generation)
- But they still need instrumentation hooks for parent-child relationships

---

## Implementation Details

### Tracing Hooks Behavior

Both generation and tracing-only methods will create AGENT spans with:
- `openinference.span.kind = "AGENT"`
- `agent.name`, `agent.method`, `agent.call_id`, `agent.parent_call_id`
- `agent.method_signature`, `agent.docstring`, `agent.file_path`
- `agent.args`, `agent.kwargs`, `agent.result`

For generation methods, additional attributes are added:
- `strategy.name`, `strategy.max_iterations`, `strategy.max_retries`

For regular Python methods (tracing-only), additional attributes are added:
- `source_code` - The full source code of the method (captured via `inspect.getsource()`)

### Parent-Child Relationships

The call stack tracking (`_push_agent_call_id`, `_pop_agent_call_id`) ensures proper parent-child relationships:

```python
class MyAgent(Agent):
    _enable_tracing = True

    async def method(self, arg: str) -> str:  # Regular Python method
        return await self.gen(arg)  # Call ellipsis method

    async def gen(self, arg: str) -> str:  # Ellipsis method
        ...
```

**Trace output:**
```
method() [AGENT span]
└── gen() [AGENT span - child of method()]
    └── LLM call [LLM span - child of gen()]
    └── code_execution [TOOL span - child of gen()]
```

### Edge Cases

1. **Strategy methods without runtime**: Continue existing behavior (unchanged)
2. **@strategy decorator**: Skip (already has `_agent_decorator` attribute)
3. **@no_trace decorator**: Respected for both generation and tracing-only methods
4. **Deadlock detection**: Still enforced (line 267)

## Testing Considerations

1. **Test all method types get traced**:
   - Public methods
   - Private methods (`_method`)
   - Dunder methods (`__method__`)
   - Regular Python methods
   - Ellipsis methods

2. **Test @no_trace works for all method types**

3. **Test parent-child relationships**:
   - Regular method calling ellipsis method
   - Ellipsis method calling regular method
   - Nested calls (multiple levels)

4. **Test backward compatibility**:
   - Classes without `_enable_tracing` should not be traced
   - Existing traced classes should continue working

## Questions

None - implementation path is clear based on existing patterns in the codebase.
