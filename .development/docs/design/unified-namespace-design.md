# Unified Namespace Design

**Status**: Design
**Parent**: [unified-runtime-plan.md](unified-runtime-plan.md) (Phase 2)
**Related**: [strategy-middleware-design.md](strategy-middleware-design.md) (strategy builtins)

## Problem

Three different places build execution namespaces with inconsistent globals:

| Location | Used For | `self` | `asyncio` | Module imports |
|----------|----------|--------|-----------|----------------|
| `SimpleExecutor._build_execution_globals()` | Generated code | ✓ | ✓ | ✓ |
| `ActorRuntime.evaluate_expression()` | Template expansion | ✓ | ✗ | ✗ |
| `PromptBuilder` (implicit) | Context blocks | ✓ | ? | ? |

**Problem**: "What can I access?" depends on context. Confusing mental model.

---

## Solution: Single Namespace Function

```python
# util/namespace.py

def make_agent_namespace(
    agent: Agent,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build standard namespace for agent code evaluation.

    This is THE SINGLE SOURCE OF TRUTH for what's available in:
    - Generated code execution
    - Context block expressions (expr="self.tools")
    - Template expansion ({len(self.tools)})
    - REPL evaluation
    """
```

---

## Namespace Layers

The execution namespace is built in layers. Each layer has a clear responsibility:

```
┌─────────────────────────────────────────────────────────────┐
│                    Namespace Assembly                        │
│                                                             │
│  1. Agent's module imports (requests, datetime, etc.)       │
│                         ↓                                   │
│  2. Core: self, asyncio, __builtins__                       │
│                         ↓                                   │
│  3. Extra: strategy builtins, method params, etc.           │
└─────────────────────────────────────────────────────────────┘
```

### Layer 1: Agent's Module Imports

Everything from the agent's source file:

```python
# my_agent.py
import requests           # Available as `requests`
from datetime import datetime  # Available as `datetime`

def helper():             # Available as `helper`
    ...

class MyAgent(Agent):
    class WorkerAgent(Agent):  # Available as `WorkerAgent`
        ...
```

### Layer 2: Core

| Global | Type | Description |
|--------|------|-------------|
| `self` | Agent | The agent instance |
| `asyncio` | module | For async/await operations |
| `__builtins__` | dict | Python builtins |

### Layer 3: Extra

Caller-provided variables via `extra` dict:
- Strategy builtins (`message`, `reasoning`)
- Method parameters from CurrentCall
- REPL locals (if applicable)

The caller decides what goes in `extra`. No magic.

---

## Utilities: Explicit Assignment

Utilities are **not magically injected**. Agents opt-in by assigning them in `__init__`:

```python
from nemo_oo_agents import Agent
from nemo_oo_agents import utils

class MyAgent(Agent):
    def __init__(self):
        # Opt-in to utilities you need
        self.task = utils.task
        self.logger = utils.logger
        self.context = utils.context
```

Generated code accesses via `self`:

```python
# In generated code
self.task.current()
self.logger.info("Processing started")
self.context.update_block("status", "running")
```

**Benefits:**
- Explicit over implicit - no magic injection
- Agents choose what they need
- Standard Python pattern
- Simpler `make_agent_namespace()` - just `self`, `asyncio`, module imports

---

## Strategy Builtins

Strategies pass their builtins via `execute_code(builtins=...)`:

```python
# In strategy.execute()
async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
    builtins = {
        "message": lambda text: runtime.history.add(
            MessageEvent(data=ContentData(content=text))
        ),
        "reasoning": lambda text: runtime.history.add(
            ReasoningEvent(data=ContentData(content=text)),
            record=self.record_reasoning
        ),
    }

    result = await runtime.execute_code(code, builtins=builtins)
```

Strategy is in control. No magic.

---

## util/ Directory

The `util/` directory contains context-aware utilities that agents can opt-in to:

```
util/
├── __init__.py      # Exports: task, context, logger
├── _context.py      # Context vars: _current_agent, _current_runtime
├── task.py          # Task introspection and control
├── context.py       # Prompt block management
├── logger.py        # Structured logging
├── doc.py           # Self-documentation utilities
└── prompt.py        # Prompt utilities
```

### Design Principles

1. **Context-aware**: Use `_current_agent()` to access agent without passing it
2. **Pure Python**: No framework magic beyond context vars
3. **Opt-in**: Agents explicitly assign utilities they need
4. **Focused**: Each module does one thing well

### Example: Adding a New Utility

```python
# util/metrics.py
"""Metrics utility for performance tracking.

Usage:
    # In agent __init__
    self.metrics = utils.metrics

    # In generated code
    self.metrics.start_timer("api_call")
    result = await call_api()
    self.metrics.end_timer("api_call")
"""

from nemo_oo_agents.util._context import _current_agent

def start_timer(name: str) -> None:
    """Start a named timer."""
    agent = _current_agent()
    # ... implementation

def record(name: str, value: float) -> None:
    """Record a metric value."""
    agent = _current_agent()
    # ... implementation
```

Then export from `util/__init__.py`:
```python
from nemo_oo_agents.util import context, logger, task, metrics
__all__ = ["context", "logger", "task", "metrics"]
```

---

## Implementation

```python
# util/namespace.py

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent

def make_agent_namespace(
    agent: Agent,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build standard namespace for agent code evaluation.

    Args:
        agent: The agent instance
        extra: Additional globals (strategy builtins, method params, etc.)

    Returns:
        Namespace dict for code execution
    """
    # Layer 1: Agent's module namespace (lowest priority)
    agent_module = inspect.getmodule(type(agent))
    namespace = dict(agent_module.__dict__) if agent_module else {}

    # Layer 2: Core
    namespace["self"] = agent
    namespace["asyncio"] = asyncio
    namespace["__builtins__"] = __builtins__

    # Layer 3: Extra (highest priority)
    if extra:
        namespace.update(extra)

    return namespace
```

### Usage in Runtime

```python
# runtime/actor.py

async def execute_code(
    self,
    code: str,
    *,
    builtins: dict[str, Any] | None = None,
) -> ExecutionResult:
    """Execute code with namespace."""
    namespace = make_agent_namespace(self.agent, extra=builtins)

    with self.tracer.code_execution_span(code):
        return await self._executor.execute(code, namespace)
```

---

## Files Changed

| File | Change |
|------|--------|
| `util/namespace.py` | **NEW**: `make_agent_namespace()` |
| `runtime/executor.py` | Delete `_build_execution_globals()`, use `make_agent_namespace()` |
| `runtime/actor.py` | Update `evaluate_expression()`, add `builtins` param to `execute_code()` |

---

## Success Criteria

1. **Single function**: Only `make_agent_namespace()` builds base namespace
2. **Clear layers**: 3 layers - module imports, core, extra
3. **Explicit utilities**: No magic injection - agents opt-in via `self.x = utils.x`
4. **Explicit builtins**: Strategy passes builtins via `execute_code(builtins=...)`

---

## Benefits

1. **One mental model** - "What can I access?" has one answer
2. **Explicit > implicit** - No magic, caller controls what's in namespace
3. **Simple implementation** - No registry, no injection logic
4. **Strategy in control** - Builtins passed explicitly, not magically injected
