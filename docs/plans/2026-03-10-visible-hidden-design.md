# Unified Visibility Model: `visible` / `hidden`

**Date:** 2026-03-10
**Status:** Approved
**Branch:** pfurgale/blocking-call-prevention-design

## Problem

Agent006 has five separate mechanisms controlling what the LLM can see:

1. Underscore prefix convention (`_private` filtered from `doc(self)`)
2. `_FRAMEWORK_ATTRS` hardcoded set
3. `@no_trace` (tracing only, not visibility)
4. `blocked_modules` / `blocked_calls` (security restrictions)
5. `_VisibleToAgent` context manager (module-level exec_globals filtering)

This is confusing. Users must learn multiple conventions with different scopes and enforcement levels.

## Design

Replace all visibility mechanisms with two concepts: `visible` and `hidden`.

`@no_trace` is orthogonal (tracing, not visibility) and stays unchanged.
`blocked_modules` / `blocked_calls` are orthogonal (security, not visibility) and stay unchanged.

### Public API

Both exported from `agent006`:

```python
from agent006 import visible, hidden
```

### The `hidden` sentinel

A single object that works in two roles:

1. **Decorator** on methods: `@hidden`
2. **Annotation marker** on fields: `Annotated[T, hidden]`

```python
# src/agent006/visibility.py

class _Hidden:
    """Marker for hiding methods and fields from the LLM."""

    def __call__(self, func):
        """Use as @hidden decorator on methods."""
        func._agent006_hidden = True
        return func

    def __repr__(self):
        return "hidden"

hidden = _Hidden()
```

Detection helpers:

- `is_hidden_method(func)` — checks for `_agent006_hidden` attribute
- `is_hidden_field(cls, name)` — inspects `typing.get_type_hints(cls, include_extras=True)`, checks if `hidden` sentinel is in `Annotated` metadata

### The `visible` context manager

Rename of the existing `_VisibleToAgent`. Controls which module-level names are exposed in `exec_globals`.

```python
# Module scope: default HIDDEN
import json
import os

from agent006 import visible

with visible:
    from pathlib import Path
    API_BASE = "https://api.example.com"
    MAX_RETRIES = 3
```

Same dict-diff mechanism, same reentrant stack. Only the name changes:
- `_VisibleToAgent` → `_Visible`
- `visible_to_agent` → `visible`

### Visibility defaults

| Scope | Default | Opt-in | Opt-out |
|-------|---------|--------|---------|
| Module level | HIDDEN | `with visible:` block | (already hidden) |
| Class methods | VISIBLE | (already visible) | `@hidden` |
| Class fields | VISIBLE | (already visible) | `Annotated[T, hidden]` |

### Full example

```python
# Module scope: default HIDDEN
import json
import os

from agent006 import Agent, hidden, visible

with visible:
    from pathlib import Path

    API_BASE = "https://api.example.com"
    MAX_RETRIES = 3

class ResearchAgent(Agent, llm=llm):
    # Class body: default VISIBLE
    model: str = "gpt-4"
    api_key: Annotated[str, hidden] = ""
    rate_limit: Annotated[int, hidden] = 5
    results: list[str] = []

    def search(self, query: str) -> list[str]:
        """Search for {query}."""
        return [f"result for {query}"]

    def _parse(self, raw: str) -> dict:
        """Not hidden — underscore no longer has visibility meaning."""
        return json.loads(raw)

    @hidden
    def rebuild_index(self):
        """Hidden from LLM — not in doc(self), not in exec_globals."""
        pass

    @hidden
    def _check_rate(self) -> bool:
        """Hidden explicitly, not by underscore convention."""
        return True
```

### What `hidden` means

"Hidden from the LLM" means:

- **Methods:** Not in `doc(self)` output. Not callable from LLM-generated code via exec_globals.
- **Fields:** Not in `doc(self)` output. Not in `__instance_values__()`.

Hidden does NOT mean enforced at runtime on `self` — if the LLM guesses a hidden attribute name, `self.api_key` still works. This is analogous to Python's own private convention: not enforced, but the LLM has no way to discover hidden names.

A proxy-based enforcement layer can be added later if needed.

### Subclass override

Annotations are class-level. To unhide a parent's hidden field, the subclass re-declares without `hidden`:

```python
class Agent:
    context: Annotated[Context, hidden]

class MyAgent(Agent, llm=llm):
    context: Context  # unhides — visible to LLM for this class
```

No `unhide()` helper. No per-instance overrides. One mechanism: class-level annotations. If you need different visibility, make a subclass.

### Framework attrs migration

`_FRAMEWORK_ATTRS` is deleted. The Agent base class fields become:

```python
class Agent:
    runtime: Annotated[Runtime, hidden]
    event_manager: Annotated[EventManager, hidden]
    event_query: Annotated[EventQuery, hidden]
    render_config: Annotated[RenderConfig, hidden]
```

## What changes

### Removed
- `_FRAMEWORK_ATTRS` hardcoded set
- Underscore prefix filtering in `__type_info__()`
- Underscore prefix filtering in `__instance_values__()`
- `_VisibleToAgent` class name
- `visible_to_agent` export name

### Added
- `hidden` sentinel (decorator + annotation marker)
- `visible` export (renamed from `visible_to_agent`)
- `is_hidden_method()` helper
- `is_hidden_field()` helper

### Changed
- `__type_info__()` — filter by `@hidden` marker instead of underscore
- `__instance_values__()` — filter by `Annotated[T, hidden]` instead of underscore / `_FRAMEWORK_ATTRS`
- Agent base class fields — annotated with `hidden` instead of listed in `_FRAMEWORK_ATTRS`

### Unchanged
- `@no_trace` — orthogonal (tracing)
- `blocked_modules` / `blocked_calls` — orthogonal (security)
- `exec_globals` filtering via `_agent006_visible_names` — same mechanism, `visible` is just the rename

## Breaking changes

- `_private` methods are no longer auto-hidden. Must use `@hidden` explicitly.
- `_private` fields are no longer auto-hidden. Must use `Annotated[T, hidden]`.
- No deprecation period. Clean break.
