# Context Blocks Library Extraction

**Status**: Design
**Phase**: 3 of [Methodic006 Overview](overview.md)
**Issues**: #10, #23, #24
**Author**: Ölf & Paul
**Date**: 2025-12-03

## Overview

Extract the context-blocks system from `agent006` into a standalone reusable library in `packages/context-blocks`. This addresses three issues:

- **#10**: Special value to conditionally hide prompt blocks
- **#23**: Base class for formatters
- **#24**: Make context blocks pydantic types, OO manager, remove unused "sections" params

## Motivation

**Why extract?** The context block system is general-purpose infrastructure with no agent006-specific dependencies. Extraction enables reuse and cleaner separation.

**Key design decisions:**

1. **Three-expression blocks** (`expr`, `update`, `show`):
   - `expr` - The expression that produces content
   - `update` - Should this expression be re-evaluated? (cache control)
   - `show` - Should this block be shown to the agent? (visibility control)

2. **String expressions with pluggable eval** - Blocks use string expressions (`expr="self.history[-10:]"`) rather than lambdas. This allows:
   - **Introspection**: Agents can see the source Python snippet that produces each block
   - **Serialization**: Expressions can be stored, transmitted, and modified as strings
   - **Debugging**: Developers can inspect what each block evaluates

   The library takes `eval: Callable[[str], Any]` per-render - no coupling to any runtime or sandbox.

3. **Async via coroutine return** - Expressions call async functions without `await` keyword (e.g., `expr="self.fetch_data()"`). `render_async()` detects coroutine results and awaits them, using `asyncio.gather` for concurrency.

4. **Two orthogonal formatters** - `BlockFormatter` (XML/Markdown) and `ProviderFormatter` (OpenAI/Anthropic) are independent. Compose any combination without combinatorial explosion.

5. **Generic Event model** - Events have `timestamp`, `type`, `data`, and optional `metadata`. Type is a flexible string. IDs for linking (e.g., tool results to tool calls) live in `data`.

6. **Library provides all formatters** - Library includes `BlockFormatter` ABC + XML/Markdown, and `ProviderFormatter` ABC + OpenAI/Anthropic implementations.

## Current State

### Files to Extract

| Current Location | LOC | Description |
|-----------------|-----|-------------|
| `src/agent006/context/renderer.py` | 306 | BlockRenderer class |
| `src/agent006/context/formats.py` | 139 | Formatters (no base class) |
| `src/agent006/context/scoped.py` | 118 | ScopedContext manager |
| `src/agent006/util/context_blocks.py` | 202 | Block manipulation helpers |
| `src/agent006/util/prompt.py` | 115 | Utility functions (preview, take, last) |
| **Total** | ~880 | |

### Files Staying in agent006

| Location | Reason |
|----------|--------|
| `src/agent006/context/prompts.py` | Agent006-specific prompt loading |
| `src/agent006/context/prompt_data/` | Agent006-specific prompt templates |

## Target Architecture

```
packages/context-blocks/
├── pyproject.toml
├── README.md
└── src/
    └── context_blocks/
        ├── __init__.py          # Public API + make_eval()
        ├── models.py            # Pydantic models: Block, BlockSection, ContextSpec, Event (#24, #10)
        ├── manager.py           # OO BlockManager (#24)
        ├── renderer.py          # BlockRenderer - render(context, eval, formatter)
        ├── formatter.py         # Formatter ABCs + implementations (XML, Markdown, OpenAI, Anthropic)
        └── scoped.py            # ScopedContext
```

**Note:** `formatter.py` contains all formatter ABCs and implementations (BlockFormatter: XML, Markdown; ProviderFormatter: OpenAI, Anthropic).

## Scope: Pluggable Rendering with Two Formatters

The library renders blocks using two orthogonal formatters:
1. **BlockFormatter**: How to format context blocks (XML tags, markdown headers, etc.)
2. **ProviderFormatter**: How to assemble context + events into provider-specific output

**Data flow:**
```
┌──────────────────────────────────────────────────────────────────────────┐
│                            BlockRenderer                                  │
│                                                                          │
│   ContextSpec                                                            │
│      │                                                                   │
│      ├──▶ context blocks ──▶ eval each ──▶ dict[str, str]               │
│      │                                           │                       │
│      │                                     BlockFormatter                │
│      │                                           │                       │
│      │                                           ▼                       │
│      │                                     context: str ─────────┐       │
│      │                                                           │       │
│      └──▶ event blocks ──▶ eval each ──▶ list[Event] ────────────┼───┐  │
│                                                                  │   │  │
│                                                          ProviderFormatter
│                                                                  │   │  │
│                                                                  ▼   ▼  │
│                                                           provider output│
│                                                     (e.g., list[dict] for│
│                                                      OpenAI, dict for    │
│                                                      Anthropic)          │
└──────────────────────────────────────────────────────────────────────────┘
```

This avoids combinatorial explosion. Instead of `OpenAIXMLFormatter`, `OpenAIMarkdownFormatter`, `AnthropicXMLFormatter`, etc., you compose:

```
BlockFormatter  ×  ProviderFormatter  →  Any combination
```

Library provides:
- **BlockFormatter**: ABC + XMLBlockFormatter, MarkdownBlockFormatter
- **ProviderFormatter**: ABC + OpenAIProviderFormatter, AnthropicProviderFormatter

### Two Sections

```python
class ContextSpec(BaseModel):
    context: BlockSection  # blocks → str (concatenated text)
    events: BlockSection   # blocks → list[Event] (generic events)
```

**Context section**: Blocks return `str`, concatenated into context text (persona, tools, instructions, etc.).
```python
Block(key="persona", expr="'You are helpful.'")
Block(key="tools", expr="self.tool_descriptions")
```

**Events section**: Blocks return `list[Event]`, concatenated.
```python
Block(key="history", expr="self.event_history[-20:]")
Block(key="current", expr="[Event(type='user', data={'content': self.input})]")
```

### Renderer Output

The renderer evaluates blocks and passes results to the formatters. Output type depends on the provider formatter:

```python
# With OpenAI provider → list[dict]
messages = renderer.render(
    spec,
    eval=my_eval,
    block_formatter=XMLBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter()
)
# [
#     {"role": "system", "content": "<persona>\nYou are helpful.\n</persona>\n\n<tools>..."},
#     {"role": "user", "content": "Hello"},
#     {"role": "assistant", "content": "Hi"},
#     {"role": "user", "content": "What's the weather?"},
#     {"role": "assistant", "content": None, "tool_calls": [...]},
#     {"role": "tool", "tool_call_id": "tc_1", "content": "Sunny, 72°F"},
#     {"role": "assistant", "content": "It's sunny and 72°F in SF."},
# ]

# With Anthropic provider → dict
result = renderer.render(
    spec,
    eval=my_eval,
    block_formatter=MarkdownBlockFormatter(),
    provider_formatter=AnthropicProviderFormatter()
)
# {
#     "system": "# Persona\n\nYou are helpful.\n\n# Tools\n\n...",
#     "messages": [...]
# }
```

**Internal flow:**
1. Renderer evaluates context blocks → `dict[str, str]`
2. BlockFormatter formats → `context: str`
3. Renderer evaluates event blocks → `list[Event]`
4. ProviderFormatter receives `(context, events)` → produces final output

### Responsibility Split

**context-blocks**:
- Block models (Block, BlockSection, ContextSpec)
- Event model (generic, model-agnostic)
- Block CRUD (BlockManager)
- Block rendering (BlockRenderer)
- All formatters (BlockFormatter, ProviderFormatter + implementations)

**agent006** (consuming application):
- Stores event history (which blocks reference via `expr`)
- Passes formatted messages to LLM API
- Can extend formatters if needed

## Design Decisions

### 1. Pydantic Block Models (#24)

Replace dict-based blocks with typed Pydantic models:

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Any

class BlockMetadata(BaseModel):
    """Metadata attached to a block."""
    custom: dict[str, Any] = {}  # User-defined metadata

class Block(BaseModel):
    """A context block that renders content for LLM prompts.

    Three expressions control block behavior:
    - expr: What to render (the content)
    - update: When to re-render (cache control)
    - show: When to show (visibility control)
    """
    key: str
    expr: str                # What to render
    update: str = "True"     # When to re-render
    show: str = "True"       # When to show (#10) - optional, default: always
    protected: bool = False
    metadata: BlockMetadata = BlockMetadata()
    last_updated: datetime | None = None

class BlockSection(BaseModel):
    """A section containing multiple blocks."""
    blocks: list[Block] = []

class ContextSpec(BaseModel):
    """Full context specification with context and events sections."""
    context: BlockSection = BlockSection()  # → str
    events: BlockSection = BlockSection()   # → list[Event]
```

### 2. Typed Event Model with Discriminated Unions

Events use Pydantic discriminated unions for type safety. Each event type has a strongly-typed data model.

```python
from pydantic import BaseModel, Field
from typing import Literal, Annotated, Union, Any
from datetime import datetime

# === Base Event ===

class EventBase(BaseModel):
    """Base class for all events."""
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = {}

# === Typed Data Models ===

class ContentData(BaseModel):
    """Data for content-based events (user, assistant messages)."""
    content: str | list  # str for text, list for multimodal

class ToolCallData(BaseModel):
    """Data for tool call events."""
    id: str
    name: str
    arguments: dict[str, Any]

class ToolResultData(BaseModel):
    """Data for tool result events."""
    tool_call_id: str
    content: str

# === Typed Events (context-blocks standard types) ===

class UserEvent(EventBase):
    """User message event."""
    type: Literal["user"] = "user"
    data: ContentData

class AssistantEvent(EventBase):
    """Assistant response event."""
    type: Literal["assistant"] = "assistant"
    data: ContentData

class ToolCallEvent(EventBase):
    """Tool invocation event."""
    type: Literal["tool_call"] = "tool_call"
    data: ToolCallData

class ToolResultEvent(EventBase):
    """Tool result event."""
    type: Literal["tool_result"] = "tool_result"
    data: ToolResultData

# === Discriminated Union ===

Event = Annotated[
    Union[UserEvent, AssistantEvent, ToolCallEvent, ToolResultEvent],
    Field(discriminator="type")
]
```

**Benefits of typed events:**
- IDE autocomplete for data fields
- Validation at construction time
- Match statements with type narrowing
- Extensible - consuming apps add their own event types

**Standard event types:**

| Type | Event Class | Data Model | Notes |
|------|-------------|------------|-------|
| `user` | `UserEvent` | `ContentData` | Text or multimodal content |
| `assistant` | `AssistantEvent` | `ContentData` | Assistant response text |
| `tool_call` | `ToolCallEvent` | `ToolCallData` | ID for linking to result |
| `tool_result` | `ToolResultEvent` | `ToolResultData` | Links to tool_call by ID |

**Example event sequence:**

```python
# User asks a question
UserEvent(data=ContentData(content="What's the weather in San Francisco?"))

# Assistant decides to call a tool
ToolCallEvent(data=ToolCallData(
    id="call_abc123",
    name="get_weather",
    arguments={"location": "San Francisco"}
))

# Tool returns result
ToolResultEvent(data=ToolResultData(
    tool_call_id="call_abc123",
    content="Sunny, 72°F, humidity 45%"
))

# Assistant responds with final answer
AssistantEvent(data=ContentData(
    content="The weather in San Francisco is sunny and 72°F with 45% humidity."
))

# Multimodal user input
UserEvent(data=ContentData(content=[
    {"type": "text", "text": "What's in this image?"},
    {"type": "image_url", "image_url": {"url": "https://..."}}
]))
```

**Type-safe pattern matching:**

```python
def handle_event(event: Event):
    match event:
        case ToolCallEvent():
            print(f"Calling {event.data.name}")  # IDE knows event.data is ToolCallData
        case AssistantEvent():
            print(event.data.content)  # IDE knows event.data is ContentData
        case UserEvent():
            print(f"User: {event.data.content}")
```

**Extending with custom events (in consuming apps):**

```python
# agent006/events.py
from context_blocks.events import EventBase, ContentData, Event as BaseEvent

class TaskEvent(EventBase):
    """Task prompt event (agent006-specific)."""
    type: Literal["task"] = "task"
    data: ContentData

class ReasoningEvent(EventBase):
    """Chain-of-thought event (agent006-specific)."""
    type: Literal["reasoning"] = "reasoning"
    data: ContentData

# Extended union for agent006
Event = BaseEvent | TaskEvent | ReasoningEvent
```

### 3. Conditional Visibility with `show` Expression (#10)

Each block has a `show` expression that determines visibility:

```python
Block(
    key="python_tools",
    expr="self.python_tools",
    update="True",
    show="self.mode != 'STRUCTURED_OUTPUT'",  # Show when NOT in structured output mode
)

Block(
    key="debug_info",
    expr="self.debug_state",
    show="self.debug_enabled",  # Show when debug is on
)

Block(
    key="history",
    expr="self.render_history()",
    # show defaults to "True" - always shown
)
```

**Evaluation order in renderer:**
1. Evaluate `show` expression first
2. If `show` is falsy → skip block entirely (don't evaluate `expr`)
3. If `show` is truthy → evaluate `update` to check cache
4. If needs re-render → evaluate `expr`

This is efficient: cheap `show` check can skip expensive `expr` evaluation.

### 4. Formatter Design: Two Orthogonal Formatters

Two separate formatter types for independent concerns:

1. **BlockFormatter** - How to format context blocks (XML, Markdown, plain)
2. **ProviderFormatter** - How to assemble context + events into provider output (OpenAI, Anthropic)

This avoids combinatorial explosion. Instead of `OpenAIXMLFormatter`, `OpenAIMarkdownFormatter`, `AnthropicXMLFormatter`, etc., you compose:

```
BlockFormatter  ×  ProviderFormatter  →  Any combination
```

**Library provides (in `context_blocks/formatter.py`):**

```python
from abc import ABC, abstractmethod
from typing import Any

class BlockFormatter(ABC):
    """Formats context blocks into a string."""

    @abstractmethod
    def format(self, blocks: dict[str, str]) -> str:
        """Format context blocks into context string."""
        pass


class XMLBlockFormatter(BlockFormatter):
    """Wraps each block in XML tags."""

    def format(self, blocks: dict[str, str]) -> str:
        parts = []
        for key, content in blocks.items():
            parts.append(f"<{key}>\n{content}\n</{key}>")
        return "\n\n".join(parts)


class MarkdownBlockFormatter(BlockFormatter):
    """Formats blocks with markdown headers."""

    def format(self, blocks: dict[str, str]) -> str:
        parts = []
        for key, content in blocks.items():
            header = key.replace("_", " ").title()
            parts.append(f"# {header}\n\n{content}")
        return "\n\n".join(parts)


class ProviderFormatter(ABC):
    """Assembles context + events into provider-specific output."""

    @abstractmethod
    def format(self, context: str, events: list[Event]) -> Any:
        """Format context string + events into provider-specific output."""
        pass


class OpenAIProviderFormatter(ProviderFormatter):
    """Assembles context + events into OpenAI message format.

    This is the default/basic formatter since OpenAI's format is widely supported.
    """

    def format(self, context: str, events: list[Event]) -> list[dict]:
        messages = [{"role": "system", "content": context}]
        for event in events:
            match event.type:
                case "user":
                    messages.append({"role": "user", "content": event.data.content})
                case "assistant":
                    messages.append({"role": "assistant", "content": event.data.content})
                case "tool_call":
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": event.data.id,
                            "type": "function",
                            "function": {
                                "name": event.data.name,
                                "arguments": json.dumps(event.data.arguments)
                            }
                        }]
                    })
                case "tool_result":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": event.data.tool_call_id,
                        "content": event.data.content
                    })
        return messages


class AnthropicProviderFormatter(ProviderFormatter):
    """Assembles context + events into Anthropic message format."""

    def format(self, context: str, events: list[Event]) -> dict:
        messages = []
        for event in events:
            match event.type:
                case "user":
                    messages.append({"role": "user", "content": event.data.content})
                case "assistant":
                    messages.append({"role": "assistant", "content": event.data.content or ""})
                case "tool_call":
                    messages.append({
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": event.data.id,
                                     "name": event.data.name, "input": event.data.arguments}]
                    })
                case "tool_result":
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": event.data.tool_call_id,
                                     "content": event.data.content}]
                    })
        return {"system": context, "messages": messages}
```

**All formatters are in the library.** Consuming apps can extend or add new ones as needed.

**Usage - compose any combination:**
```python
from context_blocks import (
    BlockRenderer,
    XMLBlockFormatter, MarkdownBlockFormatter,
    OpenAIProviderFormatter, AnthropicProviderFormatter,
)

renderer = BlockRenderer()

# OpenAI with XML context blocks
messages = renderer.render(
    spec,
    eval=my_eval,
    block_formatter=XMLBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter()
)

# OpenAI with Markdown context blocks
messages = renderer.render(
    spec,
    eval=my_eval,
    block_formatter=MarkdownBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter()
)

# Anthropic with Markdown context blocks
result = renderer.render(
    spec,
    eval=my_eval,
    block_formatter=MarkdownBlockFormatter(),
    provider_formatter=AnthropicProviderFormatter()
)
```

### 5. OO BlockManager (#24)

Replace C-style functions with an OO manager:

```python
class BlockManager:
    """Manages context blocks with CRUD operations."""

    def __init__(self, spec: ContextSpec | None = None):
        self.spec = spec or ContextSpec()

    def get(self, section: str, key: str) -> Block | None:
        """Find block by key in section."""
        ...

    def set(self, section: str, key: str, expr: str, **kwargs) -> Block:
        """Add or update a block."""
        ...

    def remove(self, section: str, key: str) -> bool:
        """Remove a block (if not protected)."""
        ...

    def find(self, section: str, predicate: Callable[[Block], bool]) -> list[Block]:
        """Find blocks matching predicate."""
        ...

    def clear_unprotected(self, section: str) -> int:
        """Remove all non-protected blocks. Returns count removed."""
        ...

    def list_keys(self, section: str, include_protected: bool = False) -> list[str]:
        """List block keys in section."""
        ...
```

### 6. Remove Unused "sections" Parameter (#24)

The `sections` parameter in `ScopedContext.__init__` is actually used (it's the `**sections` kwargs). However, we should rename for clarity:

```python
# Before
def __init__(self, context: dict, sections: dict[str, dict[str, Any]]):

# After
def __init__(self, spec: ContextSpec, blocks_by_section: dict[str, dict[str, Block | str]]):
```

## Public API

```python
# packages/context-blocks/src/context_blocks/__init__.py

from context_blocks.models import Block, BlockMetadata, BlockSection, ContextSpec
from context_blocks.events import (
    # Base
    EventBase,
    Event,
    # Data models
    ContentData,
    ToolCallData,
    ToolResultData,
    # Typed events
    UserEvent,
    AssistantEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from context_blocks.manager import BlockManager
from context_blocks.renderer import BlockRenderer
from context_blocks.formatter import (
    BlockFormatter, XMLBlockFormatter, MarkdownBlockFormatter,
    ProviderFormatter, OpenAIProviderFormatter, AnthropicProviderFormatter,
)
from context_blocks.scoped import ScopedContext

def make_eval(namespace: dict[str, Any]) -> Callable[[str], Any]:
    """Create an eval function bound to a namespace.

    Includes safe builtins (len, True, False, None, etc.) for common expressions.

    Note: agent006 uses make_agent_namespace() which provides full __builtins__,
    so this helper is primarily for standalone library usage.
    """
    safe_builtins = {
        "len": len, "str": str, "int": int, "float": float, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "True": True, "False": False, "None": None,
        "min": min, "max": max, "sum": sum, "abs": abs,
        "sorted": sorted, "reversed": reversed, "enumerate": enumerate,
        "range": range, "zip": zip, "map": map, "filter": filter,
        "any": any, "all": all, "isinstance": isinstance, "type": type,
    }
    def evaluate(expr: str) -> Any:
        return eval(expr, {"__builtins__": safe_builtins}, namespace)
    return evaluate

__all__ = [
    # Block models
    "Block",
    "BlockMetadata",
    "BlockSection",
    "ContextSpec",
    # Event base and union
    "EventBase",
    "Event",
    # Event data models
    "ContentData",
    "ToolCallData",
    "ToolResultData",
    # Typed events
    "UserEvent",
    "AssistantEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    # Manager
    "BlockManager",
    # Renderer
    "BlockRenderer",
    # BlockFormatter (context blocks → string)
    "BlockFormatter",
    "XMLBlockFormatter",
    "MarkdownBlockFormatter",
    # ProviderFormatter (context + events → provider output)
    "ProviderFormatter",
    "OpenAIProviderFormatter",
    "AnthropicProviderFormatter",
    # Scoped
    "ScopedContext",
    # Helpers
    "make_eval",
]
```

**Note:** Library exports both `BlockFormatter` and `ProviderFormatter` ABCs with implementations. Apps can extend or add new formatters as needed.

## Integration with agent006

### pyproject.toml Changes

```toml
[tool.uv.sources]
unifiedllm = { workspace = true }
context-blocks = { workspace = true }

[tool.uv.workspace]
members = [
    "packages/unifiedllm",
    "packages/context-blocks",
]
```

### Import Changes in agent006

```python
# Before
from agent006.context.renderer import BlockRenderer
from agent006.util.context_blocks import get_block, set_block

# After
from context_blocks import BlockRenderer, BlockManager, Block
```

### Migration Path

1. Create new package with all functionality
2. Update agent006 imports to use new package
3. Keep thin re-exports in agent006 for backwards compatibility (optional)
4. Remove old code from agent006

## Implementation Steps

1. **Create package structure**
   - [x] `packages/context-blocks/pyproject.toml`
   - [x] `packages/context-blocks/README.md`
   - [x] `packages/context-blocks/src/context_blocks/__init__.py`

2. **Implement models (#24, #10)**
   - [x] `models.py` - Pydantic Block (with `show` field), BlockSection, ContextSpec
   - [x] `events.py` - Typed events (UserEvent, AssistantEvent, ToolCallEvent, etc.)

3. **Implement formatter base classes**
   - [x] `formatter.py` - BlockFormatter ABC + XMLBlockFormatter, MarkdownBlockFormatter
   - [x] `formatter.py` - ProviderFormatter ABC + OpenAIProviderFormatter, AnthropicProviderFormatter

4. **Implement manager (#24)**
   - [x] `manager.py` - BlockManager class

5. **Implement renderer**
   - [x] `renderer.py` - render(context, eval, formatter), render_async()

6. **Implement scoped context**
   - [x] `scoped.py` - ScopedContext + scoped_blocks helper

7. **Update __init__.py**
   - [x] Public API exports + make_eval() helper

8. **Update agent006**
   - [x] Add workspace dependency (`context-blocks` in pyproject.toml)
   - [x] Re-export context-blocks types from `agent006.context`
   - [x] Re-export block formatters from `agent006.context.formats`
   - [x] Inline DictBlockRenderer in `runtime/prompts.py` for dict-based blocks
   - [x] context-blocks tests pass (121 passed)

9. **Clean up** ✅
   - [x] Remove old `context/renderer.py` (dict-based BlockRenderer)
   - [x] Remove old `context/scoped.py` (dict-based ScopedContext)
   - [x] Remove old `util/context_blocks.py` (dict-based helper functions)
   - [x] Delete old tests (`tests/context/test_context_blocks_new.py`)

### Status

**Phase 3 is complete:**
- context-blocks package fully implemented with 121 tests passing
- agent006 integrated with exports from context-blocks
- Old dict-based code removed (no backward compatibility)
- Inline `DictBlockRenderer` in `runtime/prompts.py` for current dict-based agent.context

**Note:** Agent006 test failures (24 failed) are from ongoing Phase 2 strategy middleware work, not Phase 3.

## Testing Strategy

The new package needs its own test suite:

```
packages/context-blocks/
└── tests/
    ├── test_models.py      # Block, BlockSection, ContextSpec, Event
    ├── test_events.py      # Event creation, linking, serialization
    ├── test_manager.py
    ├── test_renderer.py    # Renderer with formatter integration
    ├── test_formatters.py  # BlockFormatter and ProviderFormatter tests
    ├── test_scoped.py
    └── test_utils.py
```

**In agent006:**
- Integration tests for rendering with different formatter combinations
- Existing agent006 tests should continue to pass after migration

## Resolved Design Questions

1. **Package name**: `context-blocks`

2. **Evaluation**: Simple callable `(str) -> Any` passed per-render

3. **Async support**: Yes, with `asyncio.gather` for concurrent block evaluation

---

## Evaluation Design

The renderer takes an `eval` callable per-render call. Simplest possible interface:

```python
# Callable signature
Callable[[str], Any]  # expr -> result
```

### Renderer API

```python
class BlockRenderer:
    """Renders context blocks using composed formatters.

    Two orthogonal formatters:
    - BlockFormatter: How to format context blocks (XML, Markdown)
    - ProviderFormatter: How to assemble context + events for provider (OpenAI, Anthropic)

    Note: Caching implementation omitted for brevity.
    """

    def render(
        self,
        spec: ContextSpec,
        *,
        eval: Callable[[str], Any],
        block_formatter: BlockFormatter,
        provider_formatter: ProviderFormatter,
    ) -> Any:
        """Render context spec into provider-specific output.

        Args:
            spec: Context specification with context and events sections
            eval: Function that evaluates expression strings
            block_formatter: How to format context blocks (XML, Markdown)
            provider_formatter: How to assemble for provider (OpenAI, Anthropic)

        Returns:
            Provider-specific output (list[dict] for OpenAI, dict for Anthropic)
        """
        # 1. Evaluate context blocks → dict[str, str]
        context_values = {}
        for block in spec.context.blocks:
            if not eval(block.show):
                continue
            context_values[block.key] = eval(block.expr)

        # 2. Format context blocks → str
        context_str = block_formatter.format(context_values)

        # 3. Evaluate event blocks → list[Event]
        events = []
        for block in spec.events.blocks:
            if not eval(block.show):
                continue
            events.extend(eval(block.expr))

        # 4. Assemble for provider
        return provider_formatter.format(context_str, events)

    async def render_async(
        self,
        spec: ContextSpec,
        *,
        eval: Callable[[str], Awaitable[Any]],
        block_formatter: BlockFormatter,
        provider_formatter: ProviderFormatter,
    ) -> Any:
        """Render blocks concurrently using asyncio.gather.

        Same as render() but awaits async eval results concurrently.
        """
        ...
```

### Why `eval` is Required (No Default)

Python's built-in `eval()` can't be the default because:
- When called inside the renderer, it only sees the renderer's scope
- Caller's variables like `self` aren't accessible
- Only literal expressions like `"True"` or `"1+1"` would work

Therefore, `eval` is a **required parameter** - callers must provide their evaluator.

### Helper for Creating eval Functions

Library provides a helper to create eval functions bound to a namespace:

```python
def make_eval(namespace: dict[str, Any]) -> Callable[[str], Any]:
    """Create an eval function bound to a namespace.

    Args:
        namespace: Variables available to expressions (e.g., {"self": agent})

    Returns:
        Callable that evaluates expressions in the namespace

    Example:
        my_eval = make_eval({"self": agent, "datetime": datetime})
        result = my_eval("self.name")  # Returns agent.name

    Note: Includes safe builtins (len, True, False, None, etc.).
    agent006 uses make_agent_namespace() with full __builtins__ instead.
    """
    safe_builtins = {
        "len": len, "str": str, "int": int, "float": float, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "True": True, "False": False, "None": None,
        "min": min, "max": max, "sum": sum, "abs": abs,
    }
    def evaluate(expr: str) -> Any:
        return eval(expr, {"__builtins__": safe_builtins}, namespace)
    return evaluate
```

### Usage Examples

```python
from context_blocks import (
    BlockRenderer, ContextSpec, make_eval,
    XMLBlockFormatter, MarkdownBlockFormatter,
    OpenAIProviderFormatter, AnthropicProviderFormatter,
)

renderer = BlockRenderer()

# OpenAI with XML context blocks
messages = renderer.render(
    spec,
    eval=make_eval({"self": agent}),
    block_formatter=XMLBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter(),
)

# Anthropic with Markdown context blocks
result = renderer.render(
    spec,
    eval=make_eval({"self": agent}),
    block_formatter=MarkdownBlockFormatter(),
    provider_formatter=AnthropicProviderFormatter(),
)

# Custom evaluator - sandbox
messages = renderer.render(
    spec,
    eval=lambda expr: sandbox.eval(expr, {"self": agent}),
    block_formatter=XMLBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter(),
)

# Async with gather
messages = await renderer.render_async(
    spec,
    eval=lambda expr: sandbox.eval_async(expr, {"self": agent}),
    block_formatter=XMLBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter(),
)
```

### Benefits

1. **Composable**: Any BlockFormatter × ProviderFormatter combination
2. **Simplest eval interface**: Just `(str) -> Any`
3. **Per-render flexibility**: Different namespace/evaluator per call
4. **Testable**: Easy to mock eval
5. **Runtime agnostic**: Library knows nothing about how evaluation works

---

## Final Architecture

```
packages/context-blocks/
├── pyproject.toml
├── README.md
└── src/
    └── context_blocks/
        ├── __init__.py          # Public API + make_eval()
        ├── models.py            # Pydantic models (Block, BlockSection, ContextSpec)
        ├── events.py            # Typed events (EventBase, UserEvent, etc.)
        ├── manager.py           # OO BlockManager
        ├── renderer.py          # BlockRenderer (sync + async)
        ├── formatter.py         # BlockFormatter + ProviderFormatter (ABCs + implementations)
        └── scoped.py            # ScopedContext
```

## Final Public API

```python
from context_blocks import (
    # Block models
    Block,
    BlockMetadata,
    BlockSection,
    ContextSpec,
    # Event base and union
    EventBase,
    Event,
    # Event data models
    ContentData,
    ToolCallData,
    ToolResultData,
    # Typed events
    UserEvent,
    AssistantEvent,
    ToolCallEvent,
    ToolResultEvent,
    # Manager
    BlockManager,
    # Renderer
    BlockRenderer,
    # BlockFormatter (context blocks → string)
    BlockFormatter,
    XMLBlockFormatter,
    MarkdownBlockFormatter,
    # ProviderFormatter (context + events → provider output)
    ProviderFormatter,
    OpenAIProviderFormatter,
    AnthropicProviderFormatter,
    # Scoped
    ScopedContext,
    # Helpers
    make_eval,
)
```

**Note:** Library provides all formatter ABCs and implementations. Consuming apps can extend with custom event types (see "Extending with custom events" above).
