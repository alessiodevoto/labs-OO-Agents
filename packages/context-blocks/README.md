# context-blocks

Pre-resolved context block rendering for LLM applications.

## Features

- **Dynamic**: Marker for blocks that are re-evaluated each LLM turn
- **ResolvedBlock**: Pre-resolved block ready for rendering (no eval needed)
- **render_context**: Partition by role, format, truncate — no expression evaluation
- **Pluggable formatters**: XML, Markdown for blocks; OpenAI, Anthropic for providers
- **Typed event models** for conversation history

## Installation

```bash
# From the nemo_oo_agents workspace
uv sync
```

## Usage

```python
from context_blocks import (
    Dynamic, ResolvedBlock, Role, BlockMetadata,
    render_context, XMLBlockFormatter, OpenAIProviderFormatter,
)

# Create pre-resolved blocks
blocks = [
    ResolvedBlock(key="system_prompt", content="You are an assistant."),
    ResolvedBlock(key="notes", content="User prefers concise answers.",
                  metadata=BlockMetadata(expr='self.context["notes"]')),
]

# Render with formatters
messages = render_context(
    blocks,
    block_formatter=XMLBlockFormatter(),
    provider_formatter=OpenAIProviderFormatter(),
)

# Dynamic is a marker for expressions evaluated by the runtime (not by context-blocks)
marker = Dynamic("self.format_status()")
```

## Event blocks

Event blocks carry the original event on `block.event`, allowing provider
formatters to read structured data directly (e.g., `ToolCallEvent` fields
like `tool_call_id`, `name`, `arguments`, `result`).

```python
from context_blocks.events import ToolCallEvent, ToolResult

# Tool call events are passed through on the block
event = ToolCallEvent(
    tool_call_id="call_1",
    name="search",
    arguments={"query": "test"},
    result=ToolResult(tool_call_id="call_1", content="found it"),
)
block = ResolvedBlock(key="event_1", content="", role=Role.ASSISTANT, event=event)

# Provider formatters read event fields directly — no intermediate types needed
```

## Architecture

```
models.py     — Role (via roles.py), Dynamic, BlockMetadata, ResolvedBlock
events.py     — EventBase, ToolCallEvent, UserEvent, AssistantEvent, etc.
formatter.py  — BlockFormatter (XML/Markdown) + ProviderFormatter (OpenAI/Anthropic)
renderer.py   — render_context() pure function
scoped.py     — scoped_blocks() context manager
roles.py      — Role enum (shared by models and events)
```
