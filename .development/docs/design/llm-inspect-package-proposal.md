# llm-inspect: Python Introspection for LLM Agents

## Overview

**llm-inspect** is a proposed standalone Python package providing introspection utilities designed specifically for LLM/Agent consumption. The library helps agents understand and navigate Python runtime environments by providing structured, token-efficient documentation of objects, methods, variables, and types.

### Motivation

LLMs and code-generating agents need to understand their runtime context to write correct code. Current approaches (raw `help()`, `dir()`, `repr()`) produce output optimized for humans, not LLMs. We need:

1. **Token-efficient summaries** - Quick orientation without overwhelming context
2. **Actionable information** - What can I call? What parameters does it need?
3. **Structured output** - Consistent format agents can reliably parse
4. **Customizable detail** - Expand/collapse based on relevance

### Design Principles

- **Strings, not objects** - Functions return strings suitable for prompt injection
- **Expression-compatible** - Works seamlessly with context-blocks (`expr="doc(self.db)"`)
- **Composable** - Same functions work on any object; drill down with `doc(self.database)`
- **Stateless** - No expand/collapse state to manage; just call the function you need
- **Protocol-based customization** - Objects can implement magic methods for custom documentation
- **Zero dependencies** - Core library has no external dependencies (except stdlib)

---

## Function Catalog

### Priority 0 (Essential)

| Function | Purpose | Usability |
|----------|---------|-----------|
| `doc(obj)` | Full documentation | **High** - Universal need, primary entry point |
| `brief(obj)` | One-line summary | **High** - Quick orientation, minimal tokens |
| `methods(obj)` | List callable methods | **High** - "What can I call on this?" |
| `variables(obj)` | List state/attributes | **High** - "What data does this have?" |
| `imports(module)` | Available imports | **High** - "What's in scope?" |
| `schema(obj)` | Pydantic/dataclass fields | **High** - Essential for typed codebases |

### Priority 1 (Valuable)

| Function | Purpose | Usability |
|----------|---------|-----------|
| `params(func)` | Function parameters only | **Medium** - Lighter than full doc() |
| `source(func)` | Source code | **Medium** - Understanding behavior |
| `explain(exc)` | Error explanation | **Medium** - Error recovery assistance |
| `example(Type)` | Generate example instance | **Medium** - Format understanding |

### Priority 2 (Nice to Have)

| Function | Purpose | Usability |
|----------|---------|-----------|
| `hierarchy(cls)` | Inheritance tree | **Low** - Niche OOP needs |
| `available(scope)` | All names in scope | **Low** - Debugging only |

---

## API Examples

### Core Functions (P0)

```python
from llm_inspect import doc, brief, methods, variables, imports, schema

# Full documentation - comprehensive reference
doc(my_agent)
# => """# MyAgent
#
# ## Methods
# - async greet(name: str) -> str
#     Send greeting to user
# - calculate(x: int) -> int
#     Perform calculation
#
# ## Variables
# - count: int = 10
# - items: list[str] = ['a', 'b', 'c']
# """

# One-line summary - for quick orientation
brief(my_agent)
# => "MyAgent(count=10) # methods: greet(name), calculate(x)"

# Just methods - focused action discovery
methods(my_agent, detail="full")
# => """- async greet(name: str) -> str
#     Send greeting to user
# - calculate(x: int) -> int
#     Perform calculation"""

# Just state - focused data inspection
variables(my_agent)
# => """- count: int = 10
# - items: list[str] = ['a', 'b', 'c']
# - db: DatabaseClient  # methods: query, insert, update"""

# Module imports - scope awareness
imports(agent_module)
# => """Modules: json, asyncio, numpy
# Classes: DataFrame, HTTPClient
# Functions: fetch, parse"""

# Schema for structured types
schema(UserProfile)
# => """UserProfile:
#   name: str (required)
#   age: int (required)
#   email: str | None = None"""
```

### Extended Functions (P1)

```python
from llm_inspect import params, source, explain, example

# Lightweight signature
params(db.query)
# => "query(sql: str, params: dict = {}, *, timeout: float = 30.0) -> list[Row]"

# Source code inspection
source(agent.process, max_lines=30)
# => """async def process(self, items: list[str]) -> dict:
#     '''Process items and return results.'''
#     results = {}
#     for item in items:
#         results[item] = await self.handle(item)
#     return results"""

# Error explanation with context
try:
    data['user_id']
except KeyError as e:
    print(explain(e))
# => """KeyError: 'user_id'
# Context: dict has keys ['id', 'name', 'email']
# Suggestion: Did you mean 'id'?"""

# Example instance generation
example(UserProfile)
# => "UserProfile(name='example', age=0, email=None)"
```

---

## Magic Method Protocol

Objects can customize their documentation by implementing magic methods:

```python
class DatabaseClient:
    """Production database client."""

    def __init__(self, url: str, pool_size: int = 10):
        self.url = url
        self.pool_size = pool_size
        self._connection = None

    def __doc_brief__(self) -> str:
        """One-line summary for brief()."""
        status = "connected" if self._connection else "disconnected"
        return f"DatabaseClient({status}, pool={self.pool_size})"

    def __doc_full__(self) -> str:
        """Full documentation for doc()."""
        return f"""# DatabaseClient

Connection: {self.url}
Status: {"Connected" if self._connection else "Disconnected"}
Pool Size: {self.pool_size}

## Methods
- query(sql: str, params: dict = {}) -> list[Row]
- insert(table: str, data: dict) -> int
- update(table: str, data: dict, where: str) -> int
- close() -> None"""

    def __doc_schema__(self) -> str:
        """Schema representation for schema()."""
        return "DatabaseClient(url: str, pool_size: int = 10)"
```

**Protocol priority**: Magic methods take precedence over introspection. If not implemented, the library falls back to automatic introspection.

---

## Configuration

```python
from llm_inspect import DocConfig, doc

config = DocConfig(
    # Value formatting
    max_value_length=50,       # Truncate long string values
    max_list_items=10,         # Limit items shown in lists
    max_dict_items=10,         # Limit items shown in dicts

    # Source formatting
    max_source_lines=50,       # Limit source() output

    # Filtering
    hidden_prefixes=["_"],     # Hide attributes starting with _
    hidden_names={"runtime", "history"},  # Hide specific names
    include_inherited=False,   # Skip inherited methods

    # Output style
    include_types=True,        # Show type annotations
    include_defaults=True,     # Show default values
    include_docstrings=True,   # Include first line of docstrings
    include_hints=True,        # Show drill-down hints like "# doc(self.items)"
)

# Use config
doc(my_agent, config=config)

# Or set as default
import llm_inspect
llm_inspect.default_config = config
```

---

## Integration with agent006

### Direct Usage (No Wrapper)

The `Doc` class is **removed entirely**. agent006 uses `llm-inspect` functions directly in context-block expressions:

```python
from context_blocks import Block

# Before (with Doc wrapper)
Block(key="python_tools", expr="self.doc.show()")

# After (direct llm-inspect)
Block(key="python_tools", expr="doc(self)")
Block(key="request_schema", expr="schema(RequestModel)")
Block(key="available", expr="imports(self.__module__)")
```

### Composable Drill-Down (Replaces Expand/Collapse)

Instead of stateful expand/collapse, agents call functions on nested objects:

```python
# OLD (stateful, complex):
# self.doc.expand(self.database)
# self.doc.show()

# NEW (composable, simple):
doc(self)              # Overview of agent
doc(self.database)     # Detail on database
methods(self.database) # Just database methods
brief(self.items)      # Quick look at items
```

The output includes hints teaching agents they can drill down:

```markdown
## Variables
- self.items = list[5 items]  # doc(self.items)
- self.database = DatabaseClient  # methods(self.database)
```

This is **simpler and more powerful** - agents learn to use the same functions on any object, not just `self`.

### agent006-Specific Filtering

Filtering of internal attributes is handled via a pre-configured `DocConfig`:

```python
# In agent006/util/inspect_config.py (new file, ~20 lines)
from llm_inspect import DocConfig

# Agent006-specific config that hides framework internals
AGENT_DOC_CONFIG = DocConfig(
    hidden_names={"context", "runtime", "history", "history_manager",
                  "blocks", "context_spec", "prompts", "render_format"},
    hidden_prefixes=["_"],
)

# Helper function for use in expressions
def agent_doc(agent, **kwargs):
    """doc() with agent006-specific defaults."""
    return doc(agent, config=AGENT_DOC_CONFIG, **kwargs)
```

---

## Required Code Changes in agent006

### Files to Modify

| File | Change | Lines |
|------|--------|-------|
| `src/agent006/agent.py` | Remove `self.doc = Doc(self)` from `__init__`, update DEFAULT_CONTEXT_BLOCKS | ~10 |
| `src/agent006/util/doc.py` | **Delete entirely** (595 lines removed) | -595 |
| `src/agent006/util/inspect_config.py` | **Create** - agent006 config + helper | ~20 |
| `src/agent006/util/__init__.py` | Update exports | ~2 |
| `src/agent006/strategies/pure_python.py` | Update template from `{self.doc.show()}` to `{agent_doc(self)}` | ~5 |
| `tests/utils/test_doc_utility.py` | Rewrite tests for new API | ~150 |

### Detailed Changes

**1. `src/agent006/agent.py`**

```python
# REMOVE these lines from __init__:
from agent006.util.doc import Doc
self.doc: Doc = Doc(self)

# CHANGE DEFAULT_CONTEXT_BLOCKS:
# Before:
"python_tools": Block(
    key="python_tools",
    expr="self.doc.show()",
    ...
)

# After:
"python_tools": Block(
    key="python_tools",
    expr="agent_doc(self)",  # or "doc(self, config=AGENT_DOC_CONFIG)"
    ...
)
```

**2. `src/agent006/strategies/pure_python.py`**

```python
# CHANGE template (line 73):
# Before:
initial_task: str = "{instructions}\n\n{self.doc.show()}\n\n{task}..."

# After:
initial_task: str = "{instructions}\n\n{agent_doc(self)}\n\n{task}..."
```

**3. `src/agent006/util/inspect_config.py` (NEW FILE)**

```python
"""agent006-specific llm-inspect configuration."""
from llm_inspect import DocConfig, doc

AGENT_DOC_CONFIG = DocConfig(
    hidden_names={"context", "runtime", "history", "history_manager",
                  "blocks", "context_spec", "prompts", "render_format"},
    hidden_prefixes=["_"],
)

def agent_doc(agent, **kwargs):
    """doc() with agent006-specific filtering."""
    return doc(agent, config=AGENT_DOC_CONFIG, **kwargs)
```

**4. Expression namespace update**

The `agent_doc` function needs to be available in the expression evaluation namespace. This is already handled by the executor's namespace building.

### Migration Summary

| Aspect | Before | After |
|--------|--------|-------|
| Overview | `self.doc.show()` | `doc(self)` |
| Drill down | `self.doc.expand(self.items)` | `doc(self.items)` |
| Just methods | `self.doc.set(methods="full")` | `methods(self)` |
| Nested detail | N/A (expand then show) | `methods(self.database)` |
| Config | Hardcoded in Doc class | `DocConfig` parameter |
| State | Stateful (Doc instance) | Stateless (composable functions) |
| Lines of code | 595 in doc.py | ~20 in inspect_config.py |

---

## Package Structure

```
packages/llm-inspect/
├── pyproject.toml
├── README.md
├── src/
│   └── llm_inspect/
│       ├── __init__.py      # Public API exports
│       ├── core.py          # doc(), brief(), methods(), variables()
│       ├── schema.py        # schema(), example()
│       ├── source.py        # source(), params()
│       ├── errors.py        # explain()
│       ├── scope.py         # imports(), available(), hierarchy()
│       ├── config.py        # DocConfig
│       ├── protocols.py     # Magic method protocols
│       └── format.py        # Internal formatting utilities
└── tests/
    ├── test_core.py
    ├── test_schema.py
    ├── test_protocols.py
    └── test_config.py
```

---

## Implementation Phases

### Phase 1: Core Package
- Create package structure under `packages/llm-inspect/`
- Implement P0 functions: `doc()`, `brief()`, `methods()`, `variables()`, `imports()`, `schema()`
- Add `DocConfig` for customization
- Include drill-down hints in output (e.g., `# doc(self.items)`)
- Write comprehensive tests

### Phase 2: Extended Functions
- Implement P1 functions: `params()`, `source()`, `explain()`, `example()`
- Add magic method protocol support
- Documentation and examples

### Phase 3: agent006 Integration
- **Delete** `src/agent006/util/doc.py` (595 lines)
- **Create** `src/agent006/util/inspect_config.py` (~20 lines)
- **Update** `src/agent006/agent.py`:
  - Remove `self.doc = Doc(self)` from `__init__`
  - Change `DEFAULT_CONTEXT_BLOCKS["python_tools"].expr` to `agent_doc(self)`
- **Update** `src/agent006/strategies/pure_python.py` templates
- **Rewrite** tests in `tests/utils/test_doc_utility.py`
- Add `llm-inspect` to agent006 dependencies

---

## Open Questions

1. **Package name**: `llm-inspect`, `pyinspect-llm`, `llm-docs`, or something else?

2. **Output format**: Plain text (current proposal) vs structured data that can render to multiple formats?

3. **Error handling**: Should functions raise on invalid input, or return error strings?

---

## Related Work

- **Python `inspect` module**: Foundation for introspection, but output not LLM-friendly
- **`rich.inspect`**: Beautiful terminal output, but designed for humans
- **Pydantic's `model_json_schema()`**: Good for schemas, but JSON not ideal for prompts
- **`help()` / `pydoc`**: Comprehensive but verbose and unstructured

---

## Appendix: Current doc.py Analysis

The existing `agent006/util/doc.py` (595 lines) will be **deleted entirely**.

**Extract to llm-inspect (core functionality)**:
- `_format_method_summary()` / `_format_method_full()` → `methods()`
- `_format_value_summary()` / `_format_value_full()` → `variables()`
- `_render_imports()` → `imports()`
- `_format_tool_summary()` / `_format_tool_full()` → `doc()` for tool objects
- Truncation logic → `DocConfig.max_value_length`, etc.

**Move to agent006 config (20 lines)**:
- `_SYSTEM_INTERNALS` constant → `DocConfig.hidden_names`
- `_is_agent_subclass()` → Keep in agent006 if needed for child agent section

**Remove (simplify)**:
- `Doc` class and stateful expand/collapse tracking
- All the `self.doc.*` method infrastructure

**Net result**: -575 lines of code in agent006, cleaner separation of concerns
