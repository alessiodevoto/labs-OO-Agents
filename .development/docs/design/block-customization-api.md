# Block Customization API Design

**Status**: Draft
**Related**: [overview.md](overview.md), [phase-3-context-blocks.md](phase-3-context-blocks.md)

## Overview

This document specifies how agent developers customize context blocks at every level: library defaults → decorator → constructor → method → runtime.

---

## Precedence Chain

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Block Resolution Order                               │
│                                                                             │
│   1. Library Defaults     →  Sensible out-of-box behavior                   │
│   2. @agent(blocks=...)   →  Class-level customization                      │
│   3. Agent.__init__()     →  Instance-level override                        │
│   4. @plan(blocks=...)    →  Method-level override (scoped to call)         │
│   5. context.update_block →  Runtime dynamic changes                        │
│                                                                             │
│   Later levels override earlier. None removes a block.                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key principle**: Each level can override or remove blocks from previous levels.

### Hiding vs Removing Blocks

Two ways to exclude a block from rendering:

| Mechanism | Syntax | When to Use |
|-----------|--------|-------------|
| `show="False"` | `Block(expr="...", show="False")` | Block exists but hidden. Can be shown later via `show` expression. |
| `None` in config | `blocks={"history": None}` | Remove block entirely at configuration time. |

### None in Config vs None from Expression

**Important distinction:**

```python
# Config-level None: REMOVES the block (intentional)
@plan(blocks={"history": None})

# Expression evaluates to None: ERROR (likely a bug)
Block(expr="self.maybe_missing_attr")  # If this returns None → BlockEvaluationError
```

When a block's `expr` evaluates to `None` at runtime, raise `BlockEvaluationError` with a helpful message:

```
BlockEvaluationError: Block "my_block" expression evaluated to None.
  expr: self.maybe_missing_attr

If this is intentional, use show="False" to hide the block, or return an empty string.
If removing the block, set it to None in the decorator: blocks={"my_block": None}
```

This catches agent mistakes early rather than silently omitting content.

**Prefer `show`** for dynamic visibility:
```python
# Hidden by default, can be enabled by developer setting self.debug_enabled
Block(expr="self.debug_info", show="self.debug_enabled")

# Agent-controlled visibility (protected=False lets agent modify self.show_hints)
Block(expr="self.hints", show="self.show_hints", protected=False)

# Statically hidden (same as None but block still "exists")
Block(expr="...", show="False")
```

**Use `None`** when the block concept doesn't apply:
```python
# This method has no concept of history - remove it entirely
@plan(event_blocks={"history": None})
async def stateless_query(self) -> str:
    ...
```

---

## 1. Library Defaults

These are the sensible defaults that work for most agents:

```python
# Internal: agent006/runtime/defaults.py

DEFAULT_CONTEXT_BLOCKS = {
    "agent_description": Block(
        expr="self.__class__.__doc__ or ''",
        protected=True,  # Agent can't modify
    ),
    "strategy": Block(
        expr="strategy.strategy_prompt if strategy else ''",
        update="True",
        protected=True,
    ),
    "python_tools": Block(
        expr="self.doc()",
        update="True",
        protected=True,
    ),
}

DEFAULT_EVENT_BLOCKS = {
    "history": Block(
        expr="self.history_manager.recent(limit=50)",
        show="True",
        protected=True,
    ),
}
```

**Changes from current implementation:**
- Stored as dict, not list (keyed access)
- Developers can override any block at any level

**Note on `protected`:**
- `protected=True` prevents the **agent** (generated code) from modifying the block via `context.update_block()`
- Developers can always override protected blocks via decorator/constructor
- Default blocks are protected to prevent agents from accidentally breaking their own context

---

## 2. @agent Decorator — Class-Level Configuration

```python
from agent006 import Agent, agent, Block

@agent(
    llm=client,
    blocks={
        # Override default: expand specific tools
        "python_tools": Block(expr="self.doc(expand=[self.database, self.api])"),

        # Add new block (protected by default)
        "instructions": Block(expr="self.system_instructions"),

        # Add agent-modifiable block (agent can update at runtime)
        "working_context": Block(expr="self.working_context", protected=False),

        # Remove default block
        "agent_description": None,
    },
    event_blocks={
        # Scoped history by default
        "history": Block(expr="self.history_manager.for_call(current_call.id)"),
    },
)
class MyAgent(Agent):
    """Agent docstring (won't appear since agent_description=None)."""

    system_instructions: str = "You are a helpful assistant."
    working_context: str = ""  # Agent can modify this via context.update_block()
```

**API:**

```python
def agent(
    llm: LLMClient,
    *,
    blocks: dict[str, Block | None] | None = None,
    event_blocks: dict[str, Block | None] | None = None,
) -> Callable[[type[T]], type[T]]:
    """
    Decorate a class to make it an agent.

    Args:
        llm: LLM client for generation.
        blocks: Context block overrides. Keys match default block names.
                Use None to remove a default block.
        event_blocks: Event block overrides (same semantics).
    """
```

**Resolution**: Decorator blocks merge with defaults. Explicit keys override defaults. `None` values remove blocks.

---

## 3. Agent Constructor — Instance-Level Override

For when you need different configurations of the same agent class:

```python
@agent(llm=client)
class ResearchAgent(Agent):
    """Researches topics."""

    def __init__(
        self,
        *,
        depth: str = "summary",
        blocks: dict[str, Block | None] | None = None,
        event_blocks: dict[str, Block | None] | None = None,
    ):
        super().__init__(blocks=blocks, event_blocks=event_blocks)
        self.depth = depth


# Usage: different instances with different block configs
summary_agent = ResearchAgent(depth="summary")

detailed_agent = ResearchAgent(
    depth="detailed",
    blocks={
        "python_tools": Block(expr="self.doc(methods='full')"),
        "research_context": Block(expr="self.get_research_context()"),
    },
)
```

**Base class API:**

```python
class Agent:
    def __init__(
        self,
        *,
        blocks: dict[str, Block | None] | None = None,
        event_blocks: dict[str, Block | None] | None = None,
    ):
        """
        Initialize agent with optional block overrides.

        Args:
            blocks: Override context blocks from class definition.
            event_blocks: Override event blocks from class definition.
        """
```

**Resolution**: Constructor args merge with (defaults + decorator). Later overrides earlier.

---

## 4. @plan Decorator — Method-Level Override

For method-specific block configuration:

```python
@agent(llm=client)
class MyAgent(Agent):
    """Agent with method-specific contexts."""

    @plan
    async def normal_method(self, query: str) -> str:
        """Uses default blocks."""
        ...

    @plan(
        blocks={
            "extra_context": Block(expr="self.get_method_context()"),
        },
    )
    async def context_heavy_method(self) -> str:
        """Adds extra context block."""
        ...

    @plan(
        event_blocks={
            "history": None,  # Stateless - no history
        },
    )
    async def stateless_query(self, q: str) -> str:
        """Each call is independent, no conversation history."""
        ...

    @plan(
        event_blocks={
            "history": Block(expr="self.history_manager.for_call(current_call.id)"),
        },
    )
    async def scoped_method(self) -> str:
        """Only sees events from this specific call."""
        ...
```

**API:**

```python
def plan(
    strategy: GenerationStrategy | None = None,
    *,
    blocks: dict[str, Block | None] | None = None,
    event_blocks: dict[str, Block | None] | None = None,
) -> Callable:
    """
    Decorate a method to make it a plan method.

    Args:
        strategy: Generation strategy (default: PurePythonStrategy).
        blocks: Context block overrides for this method only.
        event_blocks: Event block overrides for this method only.
    """
```

**Resolution**: Method blocks are **scoped to the call** and **inherit to nested calls**. They merge with instance blocks but don't persist after the call returns.

**Inheritance**: `@plan(blocks=...)` is equivalent to wrapping the method body in `with scoped_blocks(...)`. Any `@plan` method called from within this method sees the same block configuration.

```python
@plan(event_blocks={"history": None})
async def outer_method(self) -> str:
    # No history here
    result = await self.inner_method()  # inner_method also sees no history
    return result

@plan
async def inner_method(self) -> str:
    # Inherits event_blocks={"history": None} from caller
    ...
```

---

## 5. Runtime — Dynamic Changes

For generated code to reconfigure blocks mid-execution:

```python
from agent006.util import context
from agent006 import scoped_blocks, Block

# === Permanent changes (rest of this call) ===

# Update non-protected block (works)
context.update_block("working_context", expr="'Current focus: data analysis'")

# Update protected block (raises ProtectedBlockError)
context.update_block("python_tools", expr="...")  # Error!

# Add new block (unprotected by default when agent creates it)
context.update_block("dynamic_context", expr="self.get_dynamic_data()")

# Remove non-protected block
context.remove_block("working_context")

# Get current block definition (any block)
block = context.get_block("python_tools")


# === Scoped changes (with statement) ===

# Temporarily modify blocks - restores on exit
with scoped_blocks({"history": None}):
    # No history in this scope
    result = await self.stateless_helper()
    # Nested @plan calls also see no history

with scoped_blocks({"extra": Block(expr="self.extra_context")}):
    # Extra context block available here
    result = await self.context_aware_method()

# Back to original blocks
```

**Scoped blocks inherit**: Any `@plan` method called within the `with` block sees the scoped block configuration. This enables compositional patterns where a parent method controls context for its children.

**Changes take effect on next LLM turn.**

---

## Complete Example

```python
from agent006 import Agent, agent, plan, Block

@agent(
    llm=client,
    blocks={
        # Class default: detailed tool docs
        "python_tools": Block(expr="self.doc(methods='signatures')"),
        # Add class-level instructions
        "instructions": Block(expr="self.base_instructions"),
    },
)
class ResearchAgent(Agent):
    """Research assistant that synthesizes information."""

    base_instructions: str = "Be thorough and cite sources."

    def __init__(
        self,
        *,
        verbose: bool = False,
        blocks: dict[str, Block | None] | None = None,
        **kwargs,
    ):
        # Instance can override class blocks
        instance_blocks = blocks or {}
        if verbose:
            instance_blocks.setdefault(
                "python_tools",
                Block(expr="self.doc(methods='full')")
            )
        super().__init__(blocks=instance_blocks, **kwargs)
        self.verbose = verbose

    @plan
    async def research(self, topic: str) -> str:
        """Standard research with class defaults."""
        ...

    @plan(
        event_blocks={"history": None},
    )
    async def quick_lookup(self, query: str) -> str:
        """Stateless lookup - no history context."""
        ...

    @plan(
        blocks={
            "deep_context": Block(expr="self.get_deep_context()"),
        },
    )
    async def deep_research(self, topic: str) -> str:
        """Deep research with extra context."""
        ...


# Usage
agent1 = ResearchAgent()  # Uses class defaults
agent2 = ResearchAgent(verbose=True)  # Verbose tool docs
agent3 = ResearchAgent(
    blocks={"instructions": Block(expr="'Be concise.'")}  # Override instructions
)
```

---

## Resolution Algorithm

```python
def resolve_blocks(
    defaults: dict[str, Block],
    decorator_blocks: dict[str, Block | None] | None,
    constructor_blocks: dict[str, Block | None] | None,
    method_blocks: dict[str, Block | None] | None,
) -> dict[str, Block]:
    """
    Resolve final block configuration.

    Each level can:
    - Override a block by providing Block with same key
    - Remove a block by setting key to None
    - Add new blocks with new keys
    """
    result = dict(defaults)

    for overrides in [decorator_blocks, constructor_blocks, method_blocks]:
        if overrides is None:
            continue
        for key, block in overrides.items():
            if block is None:
                result.pop(key, None)  # Remove
            else:
                result[key] = block  # Override or add

    return result
```

---

## Block Expression Context

All block expressions have access to these variables:

| Variable | Type | Description |
|----------|------|-------------|
| `self` | Agent | The agent instance |
| `strategy` | GenerationStrategy | Current strategy (in @plan methods) |
| `current_call` | CurrentCall | Current call context (in @plan methods) |
| `method` | Callable | The @plan method being executed |
| `call_args` | tuple | Positional args to the method |
| `call_kwargs` | dict | Keyword args to the method |
| `datetime` | module | Python datetime module |

**Example expressions:**

```python
# Agent state
Block(expr="self.state")
Block(expr="self.config.model_dump_json()")

# Tool documentation
Block(expr="self.doc()")
Block(expr="self.doc(expand=[self.database])")
Block(expr="self.doc(methods='full')")

# History variants
Block(expr="self.history_manager.recent(limit=100)")
Block(expr="self.history_manager.for_call(current_call.id)")
Block(expr="self.history_manager.for_call_tree(current_call.id)")

# Conditional content
Block(expr="self.debug_info if self.debug else ''")

# Strategy-aware
Block(expr="strategy.strategy_prompt if strategy else ''")

# Method-aware
Block(expr="f'Processing: {call_kwargs.get(\"topic\", \"unknown\")}'")
```

---

## Visibility Control

The `show` parameter controls whether a block is included:

```python
@agent(
    llm=client,
    blocks={
        # Always shown
        "instructions": Block(expr="self.instructions"),

        # Only when debug mode
        "debug_info": Block(
            expr="self.get_debug_info()",
            show="self.debug_enabled",
        ),

        # Only for certain strategies
        "tool_hints": Block(
            expr="self.tool_hints",
            show="strategy.name != 'StructuredOutputStrategy'",
        ),

        # Only on retry iterations
        "retry_context": Block(
            expr="self.get_retry_context()",
            show="current_call.iteration > 0",
        ),
    },
)
class MyAgent(Agent):
    debug_enabled: bool = False
```

**Evaluation order:**
1. Evaluate `show` expression
2. If falsy → skip block entirely (don't evaluate `expr`)
3. If truthy → evaluate `expr` and include block

---

## Cache Control

The `update` parameter controls when `expr` is re-evaluated:

```python
@agent(
    llm=client,
    blocks={
        # Re-evaluate every render (default)
        "dynamic_state": Block(expr="self.state", update="True"),

        # Only evaluate once (cached)
        "static_config": Block(expr="self.load_config()", update="False"),

        # Re-evaluate when condition changes
        "tools": Block(
            expr="self.doc()",
            update="self._tools_changed",
        ),
    },
)
class MyAgent(Agent):
    ...
```

---

## Migration from Current API

### Before (current)

```python
@agent(llm=client)
class MyAgent(Agent):
    """Description."""

    @plan
    async def my_method(self) -> str:
        ...

# No way to customize blocks at definition time
# Only runtime: context.update_block() from generated code
```

### After (new)

```python
@agent(
    llm=client,
    blocks={
        "python_tools": Block(expr="self.doc(expand=[self.db])"),
        "custom": Block(expr="self.custom_context"),
    },
)
class MyAgent(Agent):
    """Description."""

    @plan(event_blocks={"history": None})
    async def stateless_method(self) -> str:
        ...
```

**Backwards compatible**: Agents without explicit `blocks` parameter get library defaults (same as today, minus `protected`).

---

## Implementation Notes

### Storage

```python
class Agent:
    # Set by @agent decorator
    _class_blocks: ClassVar[dict[str, Block | None]] = {}
    _class_event_blocks: ClassVar[dict[str, Block | None]] = {}

    # Set by __init__
    _instance_blocks: dict[str, Block | None]
    _instance_event_blocks: dict[str, Block | None]

    # Resolved at runtime init
    context_spec: ContextSpec  # Final resolved blocks
```

### @plan Method Storage

```python
# Stored on the method wrapper
method._plan_blocks: dict[str, Block | None] | None
method._plan_event_blocks: dict[str, Block | None] | None
```

### Resolution Timing

- **Decorator + Constructor blocks**: Resolved once at `ActorRuntime.__init__`
- **Method blocks**: Merged per-call in `_execute_plan_method`
- **Runtime updates**: Applied immediately, take effect next render

---

## Design Decisions

1. **String expressions only** (no callables)
   - Agents need introspection - they should be able to see what blocks do
   - Strings are serializable and debuggable
   - Consistent with context-blocks library

2. **No shorthand syntax**
   - Explicit `blocks={"history": None}` over `no_history=True`
   - One way to do things, easier to learn

3. **Block scopes inherit to nested calls**
   - `@plan(blocks=...)` and `with scoped_blocks(...)` both inherit
   - `@plan(blocks=...)` is shorthand for wrapping body in `with scoped_blocks(...)`
   - Enables compositional patterns where parent controls child context
   - Matches intuition from Python's `with` statement

4. **`protected` is for agent safety, not developer restriction**
   - `protected=True` prevents **generated code** from modifying the block
   - Developers can always override any block via decorator/constructor
   - Default blocks are protected so agents can't break their own context
   - Use `protected=False` for blocks the agent should be able to modify

5. **Expression returning `None` is an error**
   - Config `None` removes block (intentional)
   - Expression evaluating to `None` raises `BlockEvaluationError` (likely bug)
   - Provides helpful error message guiding to `show="False"` or empty string
   - Catches agent mistakes early

---

## Library Boundary: context-blocks vs agent006

### context-blocks (generic library)

Reusable across any LLM application. No agent006 dependencies.

| Component | Description |
|-----------|-------------|
| `Block` | Data model (key, expr, show, update, protected, metadata) |
| `BlockSection` | Collection of blocks |
| `ContextSpec` | Container for context + event sections |
| `BlockManager` | CRUD operations: `set()`, `get()`, `remove()`, `merge()` |
| `BlockManager.scoped()` | Context manager for temporary block overrides |
| `BlockRenderer` | Evaluates blocks and formats output |
| `BlockFormatter` | XML, Markdown formatting |
| `ProviderFormatter` | OpenAI, Anthropic message assembly |
| `BlockEvaluationError` | Error when expr returns None |
| `ProtectedBlockError` | Error when modifying protected block |

### agent006 (framework-specific)

Uses context-blocks, adds agent/decorator integration.

| Component | Description |
|-----------|-------------|
| `DEFAULT_CONTEXT_BLOCKS` | agent006's sensible defaults |
| `DEFAULT_EVENT_BLOCKS` | agent006's event defaults |
| `@agent(blocks=...)` | Decorator integration |
| `@plan(blocks=...)` | Method-level integration |
| `Agent.__init__(blocks=...)` | Constructor integration |
| `scoped_blocks()` | Thin wrapper: gets current agent's BlockManager, calls `.scoped()` |
| `context.update_block()` | Thin wrapper: gets current agent's BlockManager, calls `.set()` |
| Block resolution algorithm | Merges defaults → decorator → constructor → method |
| Expression evaluation context | `self`, `strategy`, `current_call`, etc. |

### Design Principle

**context-blocks is evaluation-agnostic**. It doesn't know:
- What `self` means
- What variables are available in expressions
- How blocks are configured on classes/methods
- How to get the "current" BlockManager (that's framework's job)

**agent006 provides the glue**:
- Stores BlockManager on agent instance
- Context variable to access current agent
- Thin wrappers (`scoped_blocks()`, `context.update_block()`) that locate the right BlockManager
- Builds the `eval` function with agent namespace

---

## Open Questions

1. **Should `Block` be a dataclass or Pydantic model?**
   - Currently Pydantic in context-blocks library
   - Dataclass would be lighter for this API
   - Leaning: Keep Pydantic for consistency with library
