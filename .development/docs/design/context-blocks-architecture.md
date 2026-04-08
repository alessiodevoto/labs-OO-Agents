# Context Blocks: A Structured Approach to LLM Prompt Assembly

## The Problem

When building LLM-powered agents, prompt construction becomes surprisingly complex:

1. **Multiple content sources**: Persona definitions, tool descriptions, instructions, conversation history, retrieved documents—all must be assembled into a coherent prompt.

2. **Provider differences**: OpenAI expects `[{"role": "system", "content": "..."}, ...]` while Anthropic expects `{"system": "...", "messages": [...]}`. Same content, different shapes.

3. **Format sensitivity**: LLMs respond differently to XML tags vs Markdown headers vs plain text. The "best" format varies by model and task.

4. **Dynamic content**: Some content is static (persona), some updates occasionally (tool definitions), some changes every turn (conversation history).

The naive approach—string concatenation—quickly becomes unmaintainable. You end up with format logic mixed into content logic, provider-specific code scattered everywhere, and no clean way to change formatting decisions.

---

## Core Insight: Separate Three Concerns

We identify three distinct concerns that should be handled independently:

| Concern | Question | Examples |
|---------|----------|----------|
| **Content** | What information goes into the prompt? | Persona, tools, history, retrieved docs |
| **Format** | How is that information structured? | XML tags, Markdown, JSON, plain text |
| **Assembly** | How is it packaged for the provider? | OpenAI message list, Anthropic dict |

Mixing these concerns creates rigidity. Separating them enables:
- Change format without touching content
- Support new providers without rewriting formatters
- Test content logic independent of presentation

---

## The Two-Section Model

LLM APIs universally distinguish between:

1. **System context**: Static instructions, persona, capabilities—set once per conversation
2. **Conversation history**: The back-and-forth of user messages, assistant responses, tool calls

We mirror this with two block sections:

```
ContextSpec
├── context: BlockSection    → renders to formatted string (system message)
└── events: BlockSection     → renders to message list (conversation history)
```

**Why this split matters:**
- System context benefits from rich formatting (XML structure helps LLMs parse complex instructions)
- History is already structured (typed events: user, assistant, tool_call, tool_result)
- Providers handle them differently at the API level

---

## Architecture Options

We considered three approaches to handling the format layer:

### Option A: Dual Formatters

Separate formatters for context blocks and events:

```
                BlockRenderer
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
   BlockFormatter          EventFormatter
   (context → string)      (events → formatted events)
         │                       │
         └───────────┬───────────┘
                     ▼
             ProviderFormatter
             (assembly only)
```

**Pros:**
- Clear separation of concerns
- Context and events can have different formatting needs
- Provider layer is pure assembly (no content decisions)

**Cons:**
- Two parallel hierarchies to maintain
- Must ensure consistency between them

### Option B: Unified Formatter

Single formatter handles all content types through a unified interface:

```
                BlockRenderer
                     │
                     ▼
              ContentFormatter
              (handles both blocks and events)
                     │
                     ▼
             ProviderFormatter
```

**Pros:**
- Single source of truth for formatting rules
- Guaranteed consistency
- Simpler mental model

**Cons:**
- Mixes concerns (blocks and events have different structures)
- Harder to customize independently

### Option C: Intermediate Representation

All content passes through a unified IR before formatting:

```
    BlockRenderer
         │
         ▼
    Content → IR (typed nodes with style metadata)
         │
         ▼
    Formatter (applies styles uniformly)
         │
         ▼
    ProviderFormatter
```

**Pros:**
- True form/content separation (inspired by Microsoft's POML)
- Enables stylesheet-based configuration
- Maximally extensible

**Cons:**
- Significant complexity increase
- May be over-engineered for current needs
- Loses some type safety benefits

---

## Chosen Approach: Option A (Dual Formatters)

We chose dual formatters because:

1. **Reflects reality**: Context blocks and events ARE fundamentally different. Context is unstructured text we impose structure on. Events are already typed and structured.

2. **Clean boundaries**: BlockFormatter owns "how to present static context." EventFormatter owns "how to present conversation turns." ProviderFormatter owns "how to package for the API."

3. **Incremental adoption**: We can add EventFormatter without rewriting existing BlockFormatter logic.

4. **Future path**: If we later want unified IR (Option C), dual formatters is a stepping stone—we'd be unifying two well-defined interfaces rather than untangling mixed concerns.

---

## Component Responsibilities

### BlockRenderer
**Role**: Orchestration—evaluates blocks in order, manages caching, coordinates formatters.

- Evaluates `show` expressions (visibility control)
- Evaluates `update` expressions (cache invalidation)
- Evaluates `expr` expressions (content generation)
- Passes results to appropriate formatters
- Returns provider-ready output

### BlockFormatter
**Role**: Transform context blocks into a formatted string.

- Input: Dictionary of block key → evaluated content
- Output: Single formatted string
- Implementations: XML, Markdown, Plain

Example (XML):
```
Input:  {"persona": "You are helpful", "tools": "[list of tools]"}
Output: <persona>You are helpful</persona>\n<tools>[list of tools]</tools>
```

### EventFormatter
**Role**: Transform events into formatted representations.

- Input: Typed event (UserEvent, AssistantEvent, ToolCallEvent, ToolResultEvent)
- Output: Formatted content ready for provider assembly
- Implementations: XML, Markdown, Plain

Handles event-specific concerns:
- Tool results may need wrapping (`<tool-result>...</tool-result>`)
- Structured data may need serialization
- Multi-modal content may need special handling

### ProviderFormatter
**Role**: Assembly only—package formatted content for specific provider API.

- Input: Formatted context string + formatted events
- Output: Provider-specific structure
- Implementations: OpenAI, Anthropic, etc.

**Critical**: No content decisions here. If you're choosing how to represent something, that belongs in a Formatter, not here.

---

## Block Definition

A block is a lazy-evaluated unit of content:

| Field | Purpose |
|-------|---------|
| `key` | Unique identifier |
| `expr` | Expression that produces content (evaluated at render time) |
| `show` | Expression that controls visibility |
| `update` | Expression that controls cache invalidation |
| `protected` | Whether the block can be modified by user code |

The expression-based design enables:
- Lazy evaluation (compute only when needed)
- Dynamic content (expressions can reference runtime state)
- Conditional inclusion (show/hide based on context)

---

## Format Configuration

Inspired by POML's stylesheet concept, format rules should be declarative:

```yaml
context_blocks:
  syntax: xml
  include_metadata: true

events:
  tool_results:
    syntax: xml
    wrap_tag: tool-result
  user_messages:
    syntax: plain
```

This separates "what format to use" from "how to implement that format," enabling:
- Easy experimentation (change config, not code)
- Per-deployment customization
- A/B testing of format strategies

---

## Why Not Just Use POML?

Microsoft's POML (Prompt Orchestration Markup Language) is excellent prior art. We learned from it but diverged because:

1. **Different scope**: POML is a markup language for prompt authoring. We need a runtime system for dynamic prompt assembly in agent loops.

2. **Type safety**: Our discriminated union for events (`UserEvent | AssistantEvent | ...`) provides compile-time guarantees POML's looser structure doesn't.

3. **Integration depth**: We need tight integration with our agent runtime, tool execution, and observability systems.

4. **Incremental adoption**: We have existing code. A clean internal abstraction lets us migrate gradually.

That said, POML's core insight—separate content from presentation via stylesheet-like configuration—directly influenced our architecture.

---

## Summary

| Layer | Responsibility | Swappable? |
|-------|---------------|------------|
| Content (Blocks) | What information to include | Yes (per block) |
| Format (Formatters) | How to structure it | Yes (XML/MD/Plain) |
| Assembly (Provider) | How to package for API | Yes (OpenAI/Anthropic) |

The key insight is that these are orthogonal concerns. Any content can be formatted any way and assembled for any provider. The architecture makes these combinations explicit and independent.
