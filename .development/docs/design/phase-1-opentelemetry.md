# OpenTelemetry Instrumentation Refactor

**Status:** Phase 1-3 Complete (Core Hook Infrastructure)
**Approach:** Hook-based instrumentation following OpenInference standards
**Breaking Change:** Complete rewrite. Agent006 is pre-alpha - no backwards compatibility needed.

## Implementation Status

- [x] Phase 1: Core Hook Infrastructure (`src/nemo_oo_agents/runtime/hooks.py`)
- [x] Phase 2: Hook Call Sites (stub.py, actor.py, executor.py)
- [x] Phase 3: Delete Old Tracing (removed `src/nemo_oo_agents/tracing/`)
- [ ] Phase 4: Instrumentation Package (`util/openinference-instrumentation-nemo-oo-agents/`)
- [ ] Phase 5: Integration Testing
- [ ] Phase 6: Documentation

---

## Overview

Redesign nemo_oo_agents's OpenTelemetry tracing following patterns from `openinference-instrumentation-openai` and `openinference-instrumentation-litellm`, adapted for nemo_oo_agents's unique architecture.

**Key Decision:** Use lightweight hooks instead of wrapt (incompatible with nemo_oo_agents's instance-level method wrapping and dynamic method replacement).

**Changes:**
- Add `hooks.py` (~100 lines) - Optional callback protocol
- Modify 3 execution points (~30 lines) - Hook call sites
- Delete `src/nemo_oo_agents/tracing/` (~932 lines) - All existing tracing code
- Create `util/openinference-instrumentation-nemo-oo-agents/` - Standard instrumentation package

**Net:** -832 lines, <1% overhead when disabled, <5% when enabled

---

## Why Hooks Instead of Wrapt?

**Wrapt is incompatible** with nemo_oo_agents's architecture:

| Issue | Agent006 | Wrapt | Conflict |
|-------|----------|-------|----------|
| Method location | Instance-level (`setattr`) | Class-level | Instance shadows class |
| PERSISTENT methods | Dynamically replaced | Static wrapping | Wrapper bypassed |
| Wrapping target | Factory functions | Methods directly | Wrong target |

**Hooks solution:**
- Optional callbacks at key execution points
- Works with instance wrapping and dynamic replacement
- Standard pattern in instrumentation systems
- Near-zero overhead when not installed

---

## Architecture

### Core Changes

**New file: `src/nemo_oo_agents/runtime/hooks.py`**

```python
from typing import Any, Protocol
from contextvars import ContextVar

class InstrumentationHooks(Protocol):
    """Optional instrumentation hooks."""

    def before_agent_call(
        self, agent: Any, method_name: str, args: tuple, kwargs: dict,
        call_id: str, parent_call_id: str | None
    ) -> Any:
        """Called before @plan method execution."""
        ...

    def after_agent_call(
        self, agent: Any, method_name: str, result: Any,
        exception: Exception | None, context: Any
    ) -> None:
        """Called after @plan method completes."""
        ...

    def before_generation(
        self, agent: Any, method_name: str, strategy: str
    ) -> Any:
        """Called before LLM generation session."""
        ...

    def after_generation(
        self, agent: Any, method_name: str, result: Any,
        exception: Exception | None, context: Any
    ) -> None:
        """Called after LLM generation completes."""
        ...

    def before_code_execution(
        self, agent: Any, code: str
    ) -> Any:
        """Called before executing generated code."""
        ...

    def after_code_execution(
        self, agent: Any, code: str, result: Any,
        exception: Exception | None, context: Any
    ) -> None:
        """Called after code execution completes."""
        ...

# Global hooks storage
_instrumentation_hooks_var: ContextVar[InstrumentationHooks | None] = ContextVar(
    "instrumentation_hooks", default=None
)

def set_hooks(hooks: InstrumentationHooks | None) -> None:
    """Install or remove instrumentation hooks."""
    _instrumentation_hooks_var.set(hooks)

def get_hooks() -> InstrumentationHooks | None:
    """Get current hooks (fast context variable lookup)."""
    return _instrumentation_hooks_var.get()
```

**Design:**
- Protocol-based (type-safe, no runtime dependencies)
- Context objects passed between before/after pairs
- Simple types only (no OpenTelemetry imports in core)
- Fast context variable lookup (<1% overhead)

**Generation ID tracking:**
- Reuses existing `executor.session_id` (no duplicate IDs)
- Add `_generation_id_stack: list[str]` to `ActorRuntime` (follows `_agent_call_stack` pattern)
- Enables parent-child span linking for nested generations

### Modified Files

**1. `src/nemo_oo_agents/runtime/stub.py` - Agent call hooks**
```python
# In _wrap_plan_method wrapper function:
from nemo_oo_agents.runtime.hooks import get_hooks

hooks = get_hooks()
hook_context = None
if hooks:
    try:
        hook_context = hooks.before_agent_call(agent, method_name, args, kwargs)
    except Exception:
        pass  # Never break execution

try:
    result = await runtime.call_plan(...)  # Existing code
except Exception as e:
    exception_caught = e
    raise
finally:
    if hooks:
        try:
            hooks.after_agent_call(agent, method_name, result, exception_caught, hook_context)
        except Exception:
            pass
```

**2. `src/nemo_oo_agents/runtime/actor.py` - Generation hooks**
```python
# In _execute_with_generation:
# Add instance variable in __init__:
self._generation_id_stack: list[str] = []

# Create executor first (generates session_id)
strategy_executor = executor_class(...)

# Hook with generation ID tracking
hooks = get_hooks()
call_id = strategy_executor.session_id  # Reuse executor's ID
parent_call_id = self._generation_id_stack[-1] if self._generation_id_stack else None
self._generation_id_stack.append(call_id)

if hooks:
    try:
        hook_context = hooks.before_generation(agent, method_name, strategy, call_id, parent_call_id)
    except Exception:
        pass

try:
    result, code = await strategy_executor.execute(method)
except Exception as e:
    exception_caught = e
    raise
finally:
    if self._generation_id_stack:
        self._generation_id_stack.pop()
    if hooks:
        try:
            hooks.after_generation(agent, method_name, result, exception_caught, hook_context, call_id)
        except Exception:
            pass
```

**3. `src/nemo_oo_agents/runtime/executor.py` - Code execution hooks**
```python
# In execute_generated_code:
hooks = get_hooks()
hook_context = None
if hooks:
    try:
        hook_context = hooks.before_code_execution(agent, code)
    except Exception:
        pass

try:
    result = await self._execute_in_sandbox(code, local_vars)
except Exception as e:
    exception_caught = e
    raise
finally:
    if hooks:
        try:
            hooks.after_code_execution(agent, code, result, exception_caught, hook_context)
        except Exception:
            pass
```

**Lines added per file:**
- `hooks.py`: +100 lines (new file)
- `stub.py`: +10 lines
- `actor.py`: +20 lines (includes generation ID stack management)
- `executor.py`: +10 lines
- **Total: +140 lines**

### Deleted Files

Remove entire `src/nemo_oo_agents/tracing/` directory:
- `otel.py`, `protocol.py`, `noop.py`, `ids.py`, `jsonl_exporter.py`
- All tracing imports from runtime/executors
- OpenTelemetry dependencies from `pyproject.toml`
- **Total: -932 lines**

**Net change: -792 lines**

---

## Instrumentation Package

**Location:** `util/openinference-instrumentation-nemo-oo-agents/`

**Structure:**
```
util/openinference-instrumentation-nemo-oo-agents/
├── pyproject.toml
├── README.md
└── src/openinference/instrumentation/nemo_oo_agents/
    ├── __init__.py          # Agent006Instrumentor(BaseInstrumentor)
    ├── _hooks_impl.py       # Implements InstrumentationHooks protocol
    ├── _with_span.py        # Span lifecycle helper (from openai instrumentor)
    └── _jsonl_exporter.py   # Optional JSONL exporter
```

**Dependencies:**
- `nemo_oo_agents>=0.1.0`
- `opentelemetry-api>=1.20.0`
- `opentelemetry-sdk>=1.20.0`
- `openinference-instrumentation>=0.1.42`
- `openinference-semantic-conventions>=0.1.9`

**Key components:**

**1. `Agent006Instrumentor` (instrumentor.py)**
```python
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor

class Agent006Instrumentor(BaseInstrumentor):
    def _instrument(self, **kwargs):
        tracer_provider = kwargs.get("tracer_provider") or trace_api.get_tracer_provider()
        tracer = tracer_provider.get_tracer(__name__, __version__)

        # Create and install hooks
        hooks = OpenInferenceHooks(tracer=tracer)
        set_hooks(hooks)

    def _uninstrument(self, **kwargs):
        set_hooks(None)
```

**2. `OpenInferenceHooks` (_hooks_impl.py)**
- Implements `InstrumentationHooks` protocol
- Creates OpenTelemetry spans using tracer
- Extracts attributes following OpenInference conventions
- Uses `_WithSpan` helper for span lifecycle management
- Delegates LLM instrumentation to `openinference-instrumentation-litellm`

**3. `_WithSpan` helper (_with_span.py)**
- Copy from `openinference-instrumentation-openai`
- Manages span lifecycle with deferred attributes
- Handles OpenTelemetry's 128-attribute limit
- Two-tier attribute system (context attributes + extra attributes)

---

## Critical Patterns

### 1. Defensive Programming

**All hook calls MUST be wrapped in try/except:**
```python
if hooks:
    try:
        hooks.before_agent_call(...)
    except Exception:
        pass  # Never let instrumentation break execution
```

**Why:** Instrumentation bugs must never crash the agent. The application always takes priority.

### 2. Context Object Pattern

**Before hook returns context, after hook receives it:**
```python
# Before
hook_context = hooks.before_agent_call(agent, method_name, args, kwargs)

# After
hooks.after_agent_call(agent, method_name, result, exception, hook_context)
```

**Why:** Enables stateful instrumentation (span tracking, timing) without globals or complex state management.

### 3. Generation ID Tracking

**Why two concepts:**
- `agent_call_id` - Tracks every @plan method invocation (managed in call_plan, PERSISTENT stub, nested calls)
- `generation_id` - Tracks LLM generation sessions only (managed in _execute_with_generation)

**Example:**
```python
# First call to PERSISTENT method
await agent.method()  # → agent_call_id + generation_id (LLM generates)

# Second call (uses cached code)
await agent.method()  # → agent_call_id only (no LLM call)
```

---

## Usage Examples

### Basic Setup
```python
from opentelemetry.sdk.trace import TracerProvider
from openinference.instrumentation.nemo_oo_agents import Agent006Instrumentor

# Setup tracer provider
provider = TracerProvider()
trace.set_tracer_provider(provider)

# Instrument nemo_oo_agents
Agent006Instrumentor().instrument()

# Now all agent operations are traced
agent = MyAgent()
await agent.my_plan_method()
```

### Multi-Destination Tracing
```python
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

provider = TracerProvider()

# Console (debugging)
provider.add_span_processor(ConsoleSpanExporter())

# Arize Phoenix (UI)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces"))
)

# JSONL file (storage)
provider.add_span_processor(
    BatchSpanProcessor(JSONLSpanExporter("./traces/"))
)

trace.set_tracer_provider(provider)
Agent006Instrumentor().instrument()

# Traces go to all destinations simultaneously
```

**How it works:** Standard OpenTelemetry pattern - TracerProvider forwards spans to all registered processors. This works identically for all OpenTelemetry instrumentations.

---

## Implementation Phases

### Phase 1: Core Hook Infrastructure
**Tasks:**
1. Create `src/nemo_oo_agents/runtime/hooks.py` with protocol
2. Add `set_hooks()` / `get_hooks()` functions
3. Write unit tests
4. Document hook contract

**Deliverables:**
- `hooks.py` (~100 lines)
- Unit tests
- Documentation

### Phase 2: Hook Call Sites
**Tasks:**
1. Add `_generation_id_stack` to `ActorRuntime.__init__`
2. Modify `stub.py` - add agent call hooks
3. Modify `actor.py` - add generation hooks with ID tracking
4. Modify `executor.py` - add execution hooks
5. Wrap all hook calls in try/except
6. Write integration tests

**Deliverables:**
- Core modifications (~30 lines total)
- Integration tests

### Phase 3: Delete Old Tracing
**Tasks:**
1. Delete `src/nemo_oo_agents/tracing/` directory
2. Remove tracing imports from runtime/executors
3. Remove OpenTelemetry dependencies from `pyproject.toml`
4. Verify core tests pass without instrumentation

**Deliverables:**
- Clean core (no tracing code)
- Passing test suite

### Phase 4: Instrumentation Package
**Tasks:**
1. Create package structure in `util/`
2. Implement `Agent006Instrumentor`
3. Implement `OpenInferenceHooks`
4. Copy `_WithSpan` helper from openai instrumentor
5. Add JSONL exporter
6. Write instrumentation tests

**Deliverables:**
- Functional instrumentation package
- Test suite covering all span types

### Phase 5: Integration Testing
**Tasks:**
1. Test with different span processors
2. Test multi-destination tracing
3. Performance benchmarks
4. Test with multiple instrumentors (LiteLLM + Agent006)
5. Verify <1% overhead when disabled, <5% when enabled

**Deliverables:**
- Passing integration tests
- Performance validation

### Phase 6: Documentation
**Tasks:**
1. Update core README (hook infrastructure)
2. Write instrumentation package README
3. Update examples
4. Document breaking changes
5. Write migration guide

**Deliverables:**
- Complete documentation
- Working examples
- Migration guide

---

## Breaking Changes

### What Stops Working
All current tracing code is removed:
```python
# ❌ Old API (deleted)
from nemo_oo_agents.tracing import otel
with otel.agent_call_span(...):
    ...
```

### New API
```python
# ✅ New API (standard OpenTelemetry)
from openinference.instrumentation.nemo_oo_agents import Agent006Instrumentor

Agent006Instrumentor().instrument()
# Tracing now happens automatically via hooks
```

**Migration:** Users must install `openinference-instrumentation-nemo-oo-agents` and update code to use standard OpenTelemetry setup.

---

## Success Criteria

**Functionality:**
- ✅ All current span types captured (agent call, generation, code execution)
- ✅ All current attributes preserved
- ✅ Parent-child span relationships correct
- ✅ Multi-destination tracing works

**Performance:**
- ✅ <1% overhead when hooks not installed
- ✅ <5% overhead when instrumentation enabled
- ✅ No memory leaks

**Quality:**
- ✅ Instrumentation bugs never crash agent
- ✅ Works with other OpenTelemetry instrumentations
- ✅ Follows OpenInference semantic conventions

---

## Benefits

**Core library:**
- 800+ fewer lines of tracing code
- No OpenTelemetry dependencies
- Simpler, more focused codebase

**Instrumentation:**
- Standard OpenTelemetry patterns
- Works with entire OTel ecosystem
- Easy to extend and customize

**Users:**
- Standard setup (like other instrumentations)
- Multi-destination tracing (Phoenix, Jaeger, etc.)
- Better performance
