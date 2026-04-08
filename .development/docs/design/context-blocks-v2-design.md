# Context Blocks v2: Design Document

## Overview

This document describes the architecture for context management in Agent006, balancing two goals:
1. **LLM-managed context** - Agents can programmatically control what they see
2. **Unified block pattern** - Same `<block expr="...">` syntax in system prompt AND messages

### Pluggable Renderers

Block rendering is abstracted via the `BlockFormatter` class hierarchy:
- `XMLBlockFormatter` - Default, wraps blocks in XML tags with `expr` attributes
- `MarkdownBlockFormatter` - Uses markdown headers with inline metadata

Provider output is handled by `ProviderFormatter` implementations (`OpenAIProviderFormatter`, `AnthropicProviderFormatter`). This design allows swapping formats without changing the core architecture.

## Core Concepts

### Agents Are Python Objects

Agents hold state, have methods, and persist across calls:

```python
class MyAgent(Agent, llm=my_llm):
    def __init__(self):
        super().__init__()
        self.memory = []
        self.knowledge = {}

    async def answer(self, question: str) -> str:
        """Answer using self.memory and self.knowledge."""
        ...  # LLM generates implementation
```

### Two Context Domains

| Domain | Where | What | LLM Control |
|--------|-------|------|-------------|
| **Context Blocks** | System prompt | Agent identity, task, instructions | `context.set()`, `context.remove()` |
| **Event History** | Messages | Conversation (user, assistant, tool) | `history.events`, `history.recent()`, `history.since()`, `history.remove()`, `history.set_expr()` |

## System Prompt Structure

The system prompt is built from **context blocks**. Each block has:
- **`expr`** - What to render (Python expression)
- **`update`** - When to re-render (cache control)
- **`show`** - When to display (visibility control)

```
┌─────────────────────────────────────────────────────────┐
│ SYSTEM PROMPT                                           │
│                                                         │
│ <system_prompt expr="self._system_prompt()">            │
│   Base system instructions (protected, cached)          │
│ </system_prompt>                                        │
│                                                         │
│ <self expr="doc(self)">                                 │
│   Agent identity, methods, state                        │
│ </self>                                                 │
│                                                         │
│ <task expr="self.current_task">                         │
│   Current task description                              │
│ </task>                                                 │
│                                                         │
│ <strategy_prompt expr="self.strategy.prompt">           │
│   How to use tools, return results, etc.                │
│ </strategy_prompt>                                      │
│                                                         │
│ <context_api expr="doc(context)">                       │
│   Explains how to manage context blocks                 │
│ </context_api>                                          │
│                                                         │
│ <events_api expr="doc(events_api)">                     │
│   Explains events and how to control them               │
│   (includes current rendering expression)               │
│ </events_api>                                           │
└─────────────────────────────────────────────────────────┘
```

**Key insight:** Both system prompt AND messages use the same XML block pattern with `expr` attributes.

## Message Rendering

Events are rendered as OpenAI messages, with **XML blocks inside the content**. This maintains the same pattern as the system prompt - every piece of content has a visible `expr` showing what generated it.

### Global Event IDs

Every event has a **global ID** that remains stable regardless of filtering or slicing:

```python
history.events[42]  # Always refers to event #42, no matter what subset is rendered
```

This enables:
- **Stable references** - `history.events[42]` is the same event whether you render 10 or 100 messages
- **Evaluatable expressions** - The `expr` attributes can actually be evaluated by the LLM
- **Direct manipulation** - `history.remove(42)` removes exactly that event

### Message Block Types

| Event Type | XML Block | Notes |
|------------|-----------|-------|
| UserEvent | `<user_message expr="history.events[n].content">` | User's input |
| AssistantEvent | `<assistant_message expr="history.events[n].content">` | LLM's response |
| ToolCallEvent | `<tool_call expr="history.events[n].content" name="...">` | Code the LLM executed |
| ToolResultEvent | `<tool_result expr="history.events[n]">` | Jupyter-style: stdout first, then `Out[n]:` |

Where `n` is the **global event ID**, not a relative index.

### Output Display

**Functions `brief()` and `doc()`** are reserved for **API/type documentation only**:
- `doc(self.calculator)` - Shows method signatures and docstrings
- `brief(SomeClass)` - Concise type summary

**For execution outputs**, use:
- `Out[n]` - Access the actual Python object (can slice, iterate, access properties)
- `print(obj)` - Print content to stdout

### Example Conversation

```
┌────────────────────────────────────────────────────────────────┐
│ MESSAGES                                                       │
│                                                                │
│ <user_message expr="history.events[42].content">               │
│ Calculate the sum of sales by region                           │
│ </user_message>                                                │
│                                                                │
│ <assistant_message expr="history.events[43].content">          │
│ I'll analyze the sales data.                                   │
│ </assistant_message>                                           │
│                                                                │
│ <tool_call expr="history.events[44].content" name="execute_python">
│ data = load_csv("sales.csv")                                   │
│ data.groupby("region")["amount"].sum()                         │
│ </tool_call>                                                   │
│                                                                │
│ <tool_result expr="history.events[45]">                        │
│ Out[1]: {'North': 15000, 'South': 12000, 'East': 18000}        │
│ </tool_result>                                                 │
│                                                                │
│ <tool_call expr="history.events[46].content" name="execute_python">
│ sum(Out[-1].values())                                          │
│ </tool_call>                                                   │
│                                                                │
│ <tool_result expr="history.events[47]">                        │
│ Out[2]: 45000                                                  │
│ </tool_result>                                                 │
└────────────────────────────────────────────────────────────────┘
```

Note: The global IDs (42-47) are stable. Whether the LLM sets `history.recent(20)` or `history.recent(100)`, event 45 always refers to the same tool result.

The `<events_api>` block in the system prompt explains:
- **Current expression** rendering the messages (`history.events[-20:]`)
- **How to change it** via the history API

### LLM-Controlled Event Rendering

```python
# Default: show last 20 events
history.set_expr("history.events[-20:]")

# Reduce context when it's getting long
history.set_expr("history.events[-10:]")

# Show only recent + specific past events
history.set_expr("history.recent(10) + history.search('schema')")

# Custom filter (e.g., skip debug output)
history.set_expr("[e for e in history.events[-20:] if 'debug' not in str(e)]")
```

## The `Out[n]` Accessor

Inspired by Jupyter notebooks, `Out[n]` provides indexed access to execution outputs.

### Semantics

- **`Out[n]`** returns the **actual Python object** from execution `n`
- Negative indexing supported: `Out[-1]` is the last output
- Only executions that returned a non-None value have an `Out` entry

### Implementation

`Out` is a **view over the event history**, not separate storage:

```python
Out[n] ≡ filter(history.events, type="tool_result")[n].returned_value
```

Single source of truth (events), convenient accessor (Out).

### Examples

```python
# After executing: data = [1, 2, 3]; sum(data)
Out[-1]      # → 6 (the actual int object)
Out[-1] + 10 # → 16 (it's a real Python object)

# After executing: {"name": "Alice", "scores": [90, 85]}
Out[-1]["name"]   # → "Alice"
Out[-1]["scores"] # → [90, 85]
```

## Tool Result Display

Tool results follow **Jupyter conventions**: stdout appears first, then `Out[n]:` for return values.

### No output (e.g., `x = 5`)
```xml
<tool_result>
(executed successfully)
</tool_result>
```

### Stdout only (e.g., `print("hello")`)
```xml
<tool_result>
hello
</tool_result>
```

### Return value only (e.g., `sum([1,2,3])`)
```xml
<tool_result>
Out[1]: 6
</tool_result>
```

### Stdout + return value
```xml
<tool_result>
Processing 1000 records...
Done.

Out[2]: {'processed': 1000, 'errors': 0}
</tool_result>
```

### Large output
```xml
<tool_result>
Out[3]: [1, 2, 3, ..., 998, 999, 1000]
</tool_result>
```

### Error
```xml
<tool_result>
Error: KeyError: 'missing_column'

Traceback (most recent call last):
  File "<exec>", line 3, in <module>
KeyError: 'missing_column'
</tool_result>
```

## Context Management APIs

### Context Blocks (System Prompt)

```python
# Set/update a block
context.set("my_context", "self.relevant_data")

# Remove a block
context.remove("my_context")

# Scoped override
with context.scoped({"tools": "doc(self.calculator)"}):
    await agent.solve(problem)
```

### Event History

```python
# Access events (global IDs - stable across any filtering)
history.events[42]        # Event #42 by global ID
history.events[-1]        # Last event (relative)
history.recent(limit=10)  # Most recent N events
history.since(event_id)   # Events after a specific event

# Search history
history.search("database schema")  # Find relevant past events

# Modify history (uses global IDs)
history.remove(42)  # Remove event #42 from rendered messages

# Control what events become messages
history.set_expr("history.events[-10:]")  # Fewer events
history.set_expr("history.search('schema')")  # Filtered events
```

## Design Principles

1. **Pluggable rendering** - `BlockFormatter` hierarchy supports XML, Markdown, and custom formats
2. **Single source of truth** - Events are the source; `Out` and messages are views
3. **Transparent rendering** - All blocks (system + messages) show evaluatable `expr` attributes
4. **Global event IDs** - `history.events[42]` always refers to the same event, regardless of filtering
5. **LLM-controlled** - Both context and history have manipulation APIs
6. **Jupyter familiarity** - `Out[n]` works like Jupyter (stdout first, then return value)
7. **Clear function roles** - `brief()`/`doc()` for API docs; `print()` for output

## What the LLM Sees

```
[System Prompt]
<system_prompt expr="self._system_prompt()">
  Base system instructions
</system_prompt>

<self expr="doc(self)">
  Agent identity and capabilities
</self>

<task expr="self.current_task">
  Current task description
</task>

<context_api expr="doc(context)">
  ## Context API
  Manage context blocks in the system prompt:
  - context.set(key, expr) - Add/update a block
  - context.remove(key) - Remove a block
</context_api>

<events_api expr="doc(events_api)">
  ## Events API
  The messages below are rendered from: history.events[-20:]

  Access events (global IDs are stable across any filtering):
  - history.events[42]       # Event #42 by global ID
  - history.events[-1]       # Last event (relative)
  - history.recent(limit=10) # Most recent N events
  - history.since(event_id)  # Events after a specific event
  - history.remove(42)       # Remove event #42 from messages

  Access outputs:
  - Out[n] returns the actual Python object from execution n

  Control rendering:
  - history.set_expr("history.events[-10:]")  # fewer events
  - history.set_expr("history.search('schema')")  # specific events
</events_api>

[Messages]
<user_message expr="history.events[42].content">
Calculate the sum of sales by region
</user_message>

<assistant_message expr="history.events[43].content">
I'll analyze the sales data.
</assistant_message>

<tool_call expr="history.events[44].content" name="execute_python">
data = load_csv("sales.csv")
data.groupby("region")["amount"].sum()
</tool_call>

<tool_result expr="history.events[45]">
Out[1]: {'North': 15000, 'South': 12000, 'East': 18000}
</tool_result>

<tool_call expr="history.events[46].content" name="execute_python">
sum(Out[-1].values())
</tool_call>

<tool_result expr="history.events[47]">
Out[2]: 45000
</tool_result>
```

The LLM sees:
1. **Every block has `expr`** - Both system prompt blocks and message blocks show what generated them
2. **Global event IDs** - `history.events[45]` always refers to the same event, regardless of filtering
3. **Tool results follow Jupyter style** - stdout first, then `Out[n]:`

## Summary

| Concept | Purpose |
|---------|---------|
| **`BlockFormatter`** | Pluggable rendering (XML default, Markdown, custom) |
| **`brief()` / `doc()`** | API/type documentation only |
| **`Out[n]`** | Jupyter-style access to execution outputs |
| **`context`** | API to manage system prompt blocks |
| **`history`** | API to access/search events and control rendering |
| **Events** | Source of truth for conversation history |
| **Global event IDs** | Stable references (`history.events[42]`) that work regardless of filtering |
