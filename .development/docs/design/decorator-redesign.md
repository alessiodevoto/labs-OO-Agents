# Implementation Plan: Replace Decorators with Metaclass Auto-Wrapping

## Goal

Remove `@agent` and `@plan` decorators, replacing them with:
- **`AgentMeta` metaclass** - Auto-wraps async ellipsis methods at class creation
- **`__init_subclass__`** - Class-level configuration (replaces `@agent` decorator)
- **`@strategy` decorator** - Optional per-method strategy overrides (replaces `@plan` decorator)
- **`@no_trace` decorator** - Opt-out of tracing for public methods

## Why This Change?

**Current (Verbose):**
```python
@agent(llm=llm)
class MyAgent(Agent):
    @plan
    async def task(self): ...
```

**New (Cleaner):**
```python
class MyAgent(Agent, llm=llm):
    async def task(self): ...  # Auto-wrapped!
```

**Benefits:**
- Remove boilerplate (903 decorator usages → minimal decorators)
- More Pythonic (`__init_subclass__` is standard Python)
- Simpler mental model (less to learn)

## Core Concepts

### Generation vs Tracing (Independent)

1. **Generation** - LLM creates implementation for methods with ellipsis body
2. **Tracing** - Instrumentation hooks for observability

These are SEPARATE concerns controlled independently.

### Auto-Wrapping Rules

**Generatable Methods** (LLM generates implementation):
- ✅ Async + ellipsis body
- ✅ Includes public, private (`_name`), dunder (`__name__`)
- Uses `is_ellipsis_body()` detection (existing function)

**Traceable Methods** (instrumentation hooks):
- ✅ Public async methods (not starting with `_`)
- ✅ NOT marked with `@no_trace`
- ✅ Applies to BOTH ellipsis AND implemented methods

**Combined Matrix:**

| Method Type | Has Ellipsis? | Generated? | Traced? |
|------------|---------------|------------|---------|
| Public | Yes | ✅ | ✅ (unless `@no_trace`) |
| Public + `@no_trace` | Yes | ✅ | ❌ |
| Public | No | ❌ | ✅ (unless `@no_trace`) |
| Private (`_*`) | Yes | ✅ | ❌ (automatic) |
| Private (`_*`) | No | ❌ | ❌ |
| Dunder (`__*__`) | Yes | ✅ | ❌ (automatic) |
| Dunder (`__*__`) | No | ❌ | ❌ |

### Strategy Resolution

Per-method strategy priority:
1. **`@strategy(X)` decorator** → Use X
2. **Default** → `PurePythonStrategy()` (same as current `@plan` default)

### LLM Configuration (Two Levels)

**Class-level (REQUIRED via `__init_subclass__`):**
```python
class MyAgent(Agent, llm=my_llm):  # llm parameter REQUIRED
    ...
```

**Instance-level (OPTIONAL override in `__init__`):**
```python
agent = MyAgent(llm=custom_llm)  # Override for testing/mocking
```

Resolution order: instance param → class `_agent_llm` → parent inheritance

## Implementation Steps

### Step 1: Create Metaclass (`src/nemo_oo_agents/metaclass.py`)

**New file (~150 lines):**

```python
from nemo_oo_agents.decorators import is_ellipsis_body
import inspect
from functools import wraps

class AgentMeta(type):
    """Metaclass for auto-wrapping async ellipsis methods."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # Detect if this is Agent or GenerationStrategy subclass
        is_agent = any(hasattr(base, 'runtime') or base.__name__ == 'Agent' for base in bases)

        # Process each method in this class's namespace (not inherited)
        for attr_name, attr_value in namespace.items():
            if not inspect.iscoroutinefunction(attr_value):
                continue

            should_generate = mcs._should_generate(attr_name, attr_value)
            should_trace = mcs._should_trace(attr_name, attr_value, is_agent)

            if should_generate or should_trace:
                strategy = mcs._resolve_strategy(attr_value)
                wrapped = mcs._create_wrapper(
                    attr_value,
                    should_generate,
                    should_trace,
                    strategy
                )
                setattr(cls, attr_name, wrapped)

        return cls

    @staticmethod
    def _should_generate(method_name: str, method_obj) -> bool:
        """Check if method needs LLM generation."""
        return is_ellipsis_body(method_obj)

    @staticmethod
    def _should_trace(method_name: str, method_obj, is_agent: bool) -> bool:
        """Check if method should be traced."""
        if not is_agent:
            return False  # Never trace Strategy methods
        if method_name.startswith('_'):
            return False  # Never trace private/dunder
        if getattr(method_obj, '_no_trace', False):
            return False  # Explicit opt-out
        return True

    @staticmethod
    def _resolve_strategy(method_obj):
        """Get strategy from @strategy decorator or default."""
        if hasattr(method_obj, '_strategy_override'):
            return method_obj._strategy_override
        from nemo_oo_agents.strategies import PurePythonStrategy
        return PurePythonStrategy()

    @staticmethod
    def _create_wrapper(original_func, needs_generation, needs_tracing, strategy):
        """Create wrapper matching current @plan behavior."""
        @wraps(original_func)
        async def wrapper(self, *args, **kwargs):
            # Validate reserved parameter names
            sig = inspect.signature(original_func)
            reserved = {'reasoning', 'message'}
            if reserved & set(sig.parameters.keys()):
                raise ValueError(
                    f"{original_func.__name__} uses reserved parameter names: "
                    f"{reserved & set(sig.parameters.keys())}"
                )

            # Check for deadlock (_in_generation_session)
            if hasattr(self, '_in_generation_session') and self._in_generation_session:
                raise RuntimeError(
                    f"Cannot call {original_func.__name__} during generation"
                )

            # Duck-type detection: Agent vs Strategy context
            if hasattr(self, 'runtime'):
                # Agent method - route through runtime._call_plan()
                return await self.runtime._call_plan(
                    original_func.__name__,
                    args,
                    kwargs,
                    strategy=strategy,
                    trace=needs_tracing
                )
            else:
                # Strategy method - check for RuntimeServices parameter
                if args and hasattr(args[0], 'agent') and hasattr(args[0], 'history'):
                    runtime = args[0]
                    return await runtime.execute_nested(
                        original_func.__name__,
                        strategy=strategy
                    )
                else:
                    # Regular async method
                    return await original_func(self, *args, **kwargs)

        # Attach metadata (for introspection)
        wrapper._agent_decorator = "auto"
        wrapper._needs_generation = needs_generation
        wrapper._plan_strategy = strategy
        wrapper._original = original_func

        return wrapper

def no_trace(func):
    """Decorator to opt-out of tracing for public methods."""
    func._no_trace = True
    return func
```

**Key Implementation Notes:**
- Replicates current `@plan` wrapper logic (dual Agent/Strategy code paths)
- Validates reserved parameter names (`reasoning`, `message`)
- Handles `_in_generation_session` deadlock detection
- Duck-types to detect Agent vs Strategy context
- Attaches metadata for introspection/compatibility

### Step 2: Update `Agent` Base Class (`src/nemo_oo_agents/agent.py`)

**Add metaclass + `__init_subclass__` (~25 lines):**

```python
from nemo_oo_agents.metaclass import AgentMeta

class Agent(metaclass=AgentMeta):
    """Base class for all agents with automatic method wrapping."""

    def __init_subclass__(
        cls,
        llm: "UnifiedLLM",  # REQUIRED parameter (no default!)
        blocks: dict[str, Block | None] | None = None,
        event_blocks: dict[str, Block | None] | None = None,
        **kwargs
    ):
        """Configure agent class (replaces @agent decorator).

        Args:
            llm: REQUIRED LLM client (no default value)
            blocks: Optional context block overrides
            event_blocks: Optional event block overrides

        Usage:
            class MyAgent(Agent, llm=my_llm):
                async def task(self): ...
        """
        super().__init_subclass__(**kwargs)

        # Store class-level configuration (replaces @agent decorator)
        cls._agent_llm = llm  # Always set (required parameter)
        if blocks is not None:
            cls._agent_blocks = blocks
        if event_blocks is not None:
            cls._agent_event_blocks = event_blocks

    # Rest of implementation UNCHANGED:
    # - __init__() keeps optional llm parameter for instance overrides
    # - _resolve_llm() keeps same 3-tier resolution logic
    # - _init_blocks() unchanged
```

**Critical:** NO changes to `Agent.__init__()` or `_resolve_llm()` - existing LLM resolution logic preserved.

### Step 3: Update `GenerationStrategy` Base Class (`src/nemo_oo_agents/strategies/base.py`)

**Add same metaclass (~5 lines):**

```python
from nemo_oo_agents.metaclass import AgentMeta

class GenerationStrategy(ABC, metaclass=AgentMeta):
    """Base class for generation strategies with automatic method wrapping.

    Strategy methods with ellipsis are auto-generated (same as Agent).
    Strategy methods are NEVER traced (no .runtime attribute).
    """
    # Rest unchanged
```

**Result:** Unified pattern - both Agent and Strategy use same metaclass, same `@strategy` decorator.

### Step 4: Add `@strategy` Decorator (`src/nemo_oo_agents/decorators.py`)

**Add new decorator (~30 lines):**

```python
def strategy(
    strategy_instance=None,
    *,
    llm=None,
    blocks=None,
    event_blocks=None
):
    """Override strategy for metaclass auto-wrapping.

    Attaches metadata that AgentMeta reads during class creation.

    Args:
        strategy_instance: Strategy to use (e.g., ReflexionStrategy())
        llm: Optional LLM override for this method
        blocks: Optional context blocks override
        event_blocks: Optional event blocks override

    Usage:
        @strategy(ReflexionStrategy(max_iterations=5))
        async def my_method(self):
            ...
    """
    def decorator(func):
        # Check for duplicate @strategy decorators
        if hasattr(func, '_strategy_override'):
            raise ValueError(f"Cannot stack multiple @strategy decorators on {func.__name__}")

        func._strategy_override = strategy_instance
        func._strategy_llm = llm
        func._strategy_blocks = blocks
        func._strategy_event_blocks = event_blocks
        return func

    return decorator
```

**Remove from `decorators.py`:**
- `agent()` function (lines 122-173) - DELETE
- `plan()` function (lines 176-372) - DELETE

Net change: **-245 lines removed, +30 lines added = -215 lines**

### Step 5: Update Exports (`src/nemo_oo_agents/__init__.py`)

```python
from nemo_oo_agents.decorators import strategy  # REMOVED: agent, plan
from nemo_oo_agents.metaclass import AgentMeta, no_trace

__all__ = [
    # ... existing exports
    # REMOVE: "agent", "plan"
    "strategy",   # NEW
    "no_trace",   # NEW
    "AgentMeta",  # NEW (for advanced users)
]
```

### Step 6: Create Comprehensive Tests (`tests/test_metaclass.py`)

**New test file (~300 lines):**

```python
"""Tests for AgentMeta metaclass and auto-wrapping."""

import pytest
from nemo_oo_agents.agent import Agent
from nemo_oo_agents.metaclass import AgentMeta, no_trace
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PurePythonStrategy, ReflexionStrategy
from unifiedllm import FakeLLMClient

_TEST_LLM = FakeLLMClient()

def test_auto_wrap_public_ellipsis():
    """Public ellipsis methods are auto-wrapped."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def task(self): ...

    assert hasattr(TestAgent.task, '_agent_decorator')
    assert TestAgent.task._agent_decorator == "auto"
    assert TestAgent.task._needs_generation is True

def test_no_wrap_public_implemented():
    """Public implemented methods are NOT generated (but are traced)."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def task(self):
            return "implemented"

    # Should still be wrapped for tracing
    assert hasattr(TestAgent.task, '_agent_decorator')
    # But _needs_generation should be False
    assert TestAgent.task._needs_generation is False

def test_private_ellipsis_generated_not_traced():
    """Private ellipsis methods are generated but NOT traced."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def _helper(self): ...

    assert TestAgent._helper._needs_generation is True
    # Check wrapper doesn't add tracing hooks

def test_no_trace_decorator():
    """@no_trace prevents tracing but not generation."""
    class TestAgent(Agent, llm=_TEST_LLM):
        @no_trace
        async def task(self): ...

    assert TestAgent.task._needs_generation is True
    # Check wrapper doesn't add tracing hooks

def test_strategy_decorator_override():
    """@strategy overrides default strategy."""
    reflexion = ReflexionStrategy(max_iterations=3)

    class TestAgent(Agent, llm=_TEST_LLM):
        @strategy(reflexion)
        async def task(self): ...

    assert TestAgent.task._plan_strategy is reflexion

def test_default_strategy():
    """Methods default to PurePythonStrategy."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def task(self): ...

    assert isinstance(TestAgent.task._plan_strategy, PurePythonStrategy)

def test_llm_required():
    """Agent subclass must specify llm parameter."""
    with pytest.raises(TypeError, match="llm"):
        class TestAgent(Agent):  # Missing llm!
            async def task(self): ...

def test_llm_instance_override():
    """Instance llm parameter overrides class llm."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def task(self): ...

    custom_llm = FakeLLMClient()
    agent = TestAgent(llm=custom_llm)
    assert agent._llm is custom_llm

def test_reserved_parameter_names():
    """Methods cannot use reserved parameter names."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def bad_task(self, reasoning: str): ...

    agent = TestAgent()
    with pytest.raises(ValueError, match="reserved parameter names"):
        await agent.bad_task("test")

def test_dunder_methods():
    """Dunder ellipsis methods are generated but not traced."""
    class TestAgent(Agent, llm=_TEST_LLM):
        async def __custom__(self): ...

    assert TestAgent.__custom__._needs_generation is True

def test_method_inheritance():
    """Overridden methods wrap independently."""
    class BaseAgent(Agent, llm=_TEST_LLM):
        async def task(self): ...

    class ChildAgent(BaseAgent, llm=_TEST_LLM):
        async def task(self): ...

    # Both should be wrapped
    assert hasattr(BaseAgent.task, '_agent_decorator')
    assert hasattr(ChildAgent.task, '_agent_decorator')

def test_strategy_class_autowrap():
    """GenerationStrategy methods also auto-wrap."""
    from nemo_oo_agents.strategies.base import GenerationStrategy

    class CustomStrategy(GenerationStrategy):
        async def generate(self, runtime): ...

    assert hasattr(CustomStrategy.generate, '_agent_decorator')
    assert CustomStrategy.generate._needs_generation is True

# Additional edge case tests...
```

### Step 7: Create Migration Script (`scripts/migrate_decorators.py`)

**New script (~120 lines):**

```python
#!/usr/bin/env python3
"""Automated migration script for @agent and @plan decorators."""

import re
import ast
from pathlib import Path
from typing import Optional

def extract_agent_params(decorator_text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract llm, blocks, event_blocks from @agent decorator."""
    try:
        fake_call = f"agent({decorator_text})"
        tree = ast.parse(fake_call)
        call = tree.body[0].value

        llm_param = None
        blocks_param = None
        event_blocks_param = None

        for keyword in call.keywords:
            if keyword.arg == "llm":
                llm_param = ast.unparse(keyword.value)
            elif keyword.arg == "blocks":
                blocks_param = ast.unparse(keyword.value)
            elif keyword.arg == "event_blocks":
                event_blocks_param = ast.unparse(keyword.value)

        return llm_param, blocks_param, event_blocks_param
    except:
        return None, None, None

def migrate_file(file_path: Path) -> bool:
    """Migrate a single file. Returns True if changes made."""
    content = file_path.read_text()
    original_content = content

    # Step 1: Migrate @agent decorator to class parameters
    agent_pattern = r'@agent\(([^)]+)\)\s*\nclass\s+(\w+)\(Agent'

    def replace_agent(match):
        params_str = match.group(1)
        class_name = match.group(2)
        llm, blocks, event_blocks = extract_agent_params(params_str)

        class_params = []
        if llm:
            class_params.append(f"llm={llm}")
        if blocks:
            class_params.append(f"blocks={blocks}")
        if event_blocks:
            class_params.append(f"event_blocks={event_blocks}")

        if class_params:
            return f"class {class_name}(Agent, {', '.join(class_params)})"
        else:
            return f"class {class_name}(Agent)"

    content = re.sub(agent_pattern, replace_agent, content)

    # Step 2: Migrate @plan decorator
    # Pattern 1: @plan(strategy=X) → @strategy(X)
    content = re.sub(r'@plan\(strategy=([^)]+)\)', r'@strategy(\1)', content)

    # Pattern 2: @plan(other_params) → @strategy(other_params)
    content = re.sub(r'@plan\(([^)]+)\)', r'@strategy(\1)', content)

    # Pattern 3: @plan() or @plan → remove
    content = re.sub(r'@plan\(\)\s*\n', '', content)
    content = re.sub(r'@plan\s*\n', '', content)

    # Step 3: Update imports
    if '@strategy' in content:
        content = re.sub(
            r'from nemo_oo_agents import (.*\b)(agent|plan)(\b.*)',
            r'from nemo_oo_agents import \1strategy\3',
            content
        )
    else:
        content = re.sub(
            r'from nemo_oo_agents import (.*\b)(agent|plan)(,\s*|\b)',
            r'from nemo_oo_agents import \1',
            content
        )

    if content != original_content:
        file_path.write_text(content)
        return True
    return False

# Run migration
if __name__ == "__main__":
    import sys

    root = Path('.') if len(sys.argv) < 2 else Path(sys.argv[1])

    changed_files = []
    for py_file in root.rglob('*.py'):
        if 'venv' not in str(py_file) and '.venv' not in str(py_file):
            try:
                if migrate_file(py_file):
                    changed_files.append(py_file)
                    print(f"✓ Migrated: {py_file}")
            except Exception as e:
                print(f"✗ Error migrating {py_file}: {e}")

    print(f"\n{len(changed_files)} files migrated")
```

**Usage:**
```bash
python scripts/migrate_decorators.py
# Review changes with: git diff
# Test: pytest tests/
```

## Migration Scope

**903 total decorator usages to migrate:**
- 306 `@agent` usages across 104 files
- 597 `@plan` usages across 141 files

**High-priority files:**
- `examples/*.py` - 7 files
- `agents/*/` - 3 agent implementations
- `tests/external/test_decorators.py` - Decorator-specific tests (needs rewrite)
- `src/nemo_oo_agents/strategies/*.py` - 6 strategy files

**Migration strategy:**
1. Run automated script
2. Manual review (check git diff)
3. Run tests (`pytest tests/`)
4. Fix failing tests
5. Remove old decorator code
6. Update documentation

## Edge Cases Handled

### Decorator Stacking
- `@strategy` + `@no_trace` = ALLOWED (common pattern)
- Multiple `@strategy` = ERROR (enforced in decorator)
- Multiple `@no_trace` = Harmless (no error)

### Method Inheritance
- Each class processes own namespace independently
- Overridden methods wrap separately (no conflicts)

### Multiple Inheritance
- Works with `**kwargs` in `__init_subclass__`
- Example: `class MyAgent(Agent, OtherBase, llm=x)` ✅

### Sync vs Async
- Metaclass only wraps `async def` methods
- Sync methods ignored (no wrapping)

### Reserved Parameters
- Validates against `{'reasoning', 'message'}` in wrapper
- Raises `ValueError` at call time (same as current `@plan`)

## Testing Strategy

1. **Unit tests** - `test_metaclass.py` covers all wrapping logic
2. **Integration tests** - Existing tests should pass after migration
3. **Decorator tests** - Rewrite `tests/external/test_decorators.py`
4. **Real agents** - Test librarian-agent, tpm-agent manually
5. **Performance** - Benchmark (expect no measurable impact)

## Success Criteria

- ✅ All tests pass after migration
- ✅ Real agents (librarian, tpm-agent) work correctly
- ✅ No measurable performance regression
- ✅ Migration script handles >95% of cases automatically
- ✅ Clean git history (one migration commit, one cleanup commit)

## Implementation Order

### Phase 1: Foundation (Keep Old Decorators)
1. Create `src/nemo_oo_agents/metaclass.py`
2. Add `@strategy` decorator to `decorators.py` (keep `@agent` and `@plan` temporarily)
3. Apply metaclass to `Agent` and `GenerationStrategy`
4. Add `__init_subclass__` to `Agent`
5. Create `tests/test_metaclass.py`
6. Run tests (new + old should both work)

### Phase 2: Migration
7. Create `scripts/migrate_decorators.py`
8. Run migration script
9. Review changes (`git diff`)
10. Fix edge cases manually
11. Run full test suite
12. Fix failing tests

### Phase 3: Cleanup
13. Remove `agent()` from `decorators.py`
14. Remove `plan()` from `decorators.py`
15. Update `__init__.py` exports
16. Update documentation
17. Final test run

## Critical Files

### Files to Create
- `/home/cschueller/code/nemo_oo_agents/src/nemo_oo_agents/metaclass.py` (~150 lines)
- `/home/cschueller/code/nemo_oo_agents/tests/test_metaclass.py` (~300 lines)
- `/home/cschueller/code/nemo_oo_agents/scripts/migrate_decorators.py` (~120 lines)

### Files to Modify
- `/home/cschueller/code/nemo_oo_agents/src/nemo_oo_agents/agent.py` (+25 lines: metaclass + `__init_subclass__`)
- `/home/cschueller/code/nemo_oo_agents/src/nemo_oo_agents/strategies/base.py` (+5 lines: metaclass)
- `/home/cschueller/code/nemo_oo_agents/src/nemo_oo_agents/decorators.py` (-215 lines net: remove agent+plan, add strategy)
- `/home/cschueller/code/nemo_oo_agents/src/nemo_oo_agents/__init__.py` (update exports)

### Files Requiring Manual Attention
- `/home/cschueller/code/nemo_oo_agents/tests/external/test_decorators.py` - Rewrite decorator tests
- `/home/cschueller/code/nemo_oo_agents/agents/tpm-agent/tpm_agent.py` - 11 `@plan` usages
- `/home/cschueller/code/nemo_oo_agents/agents/librarian-agent/librarian_agent.py` - 4 `@plan` usages
- `/home/cschueller/code/nemo_oo_agents/docs/guides/writing-generation-methods.md` - Update guide

## Usage Examples

### Basic Usage
```python
# Before
@agent(llm=llm)
class MyAgent(Agent):
    @plan
    async def task(self): ...

# After
class MyAgent(Agent, llm=llm):
    async def task(self): ...  # Auto-wrapped!
```

### Strategy Override
```python
# Before
@agent(llm=llm)
class MyAgent(Agent):
    @plan(strategy=ReflexionStrategy(max_iterations=5))
    async def task(self): ...

# After
class MyAgent(Agent, llm=llm):
    @strategy(ReflexionStrategy(max_iterations=5))
    async def task(self): ...
```

### Opt-Out of Tracing
```python
class MyAgent(Agent, llm=llm):
    async def main_task(self):
        """Public → Generated + Traced"""
        ...

    @no_trace
    async def utility(self):
        """Public + @no_trace → Generated, NOT traced"""
        ...

    async def _internal(self):
        """Private → Generated, NOT traced (automatic)"""
        ...
```

### Instance LLM Override
```python
class MyAgent(Agent, llm=default_llm):
    async def task(self): ...

agent1 = MyAgent()                   # Uses default_llm
agent2 = MyAgent(llm=custom_llm)     # Override for testing
```

## Next Steps

Ready to begin implementation - Phase 1 (Foundation) can start immediately.
