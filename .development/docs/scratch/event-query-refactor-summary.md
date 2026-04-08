# EventQuery Refactor Summary

**Date**: 2026-02-13
**Status**: Core Implementation Complete ✅

## Overview

Replaced dict-based event injection with type-safe `EventQuery` for filtering events shown in context.

### Before (Dict-based Injection)
```python
# Injected events as text blocks - wrong design
@strategy(context=ScopedContext(events={"reminder": "Be thorough"}))
```

### After (EventQuery Filtering)
```python
# Filters actual events - correct design
@strategy(context=ScopedContext(events=EventQuery.current_call()))
```

## Key Changes

### 1. EventQuery Class
- **File**: `src/agent006/runtime/event_query.py`
- **Purpose**: Type-safe event filtering configuration
- **API**: Mirrors `event_manager.filter()` parameters
  - `type`: Filter by event type
  - `call_id`: Filter by call ID (use "current" for current method)
  - `query`: Text search
  - `regex`: Regex mode
  - `limit`: Max events

**Helper Methods**:
- `EventQuery.current_call()` - Filter to current method's events only
- `EventQuery.by_type(type)` - Filter by event type
- `EventQuery.last_n(n)` - Show last N events

### 2. ScopedContext Updated
- **File**: `packages/context-blocks/src/context_blocks/scoped.py`
- **Change**: `events` parameter now accepts `EventQuery | None` instead of `dict`
- **Semantics**: Child EventQuery **replaces** parent (not merged)

### 3. Context Builder Refactored
- **File**: `src/agent006/runtime/context_builder.py`
- **Removed**: Phase 7 (decorator events injection) and Phase 8 (scoped events injection)
- **Updated**: Phase 6 (Events) now uses EventQuery with 4-level priority

**Priority System**:
1. **Runtime** (`event_manager.set_event_query()`) - Highest
2. **Scoped** (`with ScopedContext(events=...)`) - High
3. **Decorator** (`@strategy(ScopedContext(events=...))`) - Medium
4. **Agent default** (class/instance level) - Low
5. **No filter** (show all events) - Default

### 4. Actor Runtime Updated
- **File**: `src/agent006/runtime/actor.py`
- **Changed**: `_decorator_events_var` type from `dict` to `EventQuery`
- **Updated**: Event query merging logic (child replaces parent)
- **Added**: Passes `current_call_id` to context builder for "current" resolution

### 5. Exports
- `EventQuery` available from `agent006` and `agent006.runtime`
- Updated decorator examples to show EventQuery usage

## Architecture

### Event Filtering Flow

```
┌─────────────────────────────────────────┐
│   Decorator/Scoped/Agent EventQuery     │
│   (3-level priority: scoped > dec > agent) │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  _phase_events() in context_builder.py  │
│  - Determines active EventQuery          │
│  - Calls event_manager.filter()          │
│  - Applies EventQuery.apply() if present │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│    Filtered events → ResolvedBlocks     │
│    Rendered normally in context          │
└─────────────────────────────────────────┘
```

## Usage Examples

### Method-Scoped Event Filtering
```python
from agent006 import Agent, strategy, EventQuery
from context_blocks import ScopedContext

class MyAgent(Agent, llm=llm):
    @strategy(context=ScopedContext(
        events=EventQuery.current_call()
    ))
    async def solve_with_reflection(self, problem: str):
        """LLM sees only events from this method execution."""
        ...
```

### Last N Events
```python
@strategy(context=ScopedContext(
    events=EventQuery.last_n(10)
))
async def analyze(self, data: str):
    """LLM sees only last 10 events."""
    ...
```

### Error Events Only
```python
@strategy(context=ScopedContext(
    events=EventQuery.by_type("Error")
))
async def debug_errors(self):
    """LLM sees only Error events."""
    ...
```

### Runtime Override with ScopedContext
```python
async def my_method(self):
    # Temporarily show only current method's events
    with ScopedContext(events=EventQuery.current_call()):
        await self.nested_method()
```

### Runtime Override with EventManager
```python
async def my_method(self):
    # Set runtime query (highest priority - overrides all others)
    self.event_manager.set_event_query(EventQuery.by_type("Error"))

    try:
        await self.process_data()  # LLM sees only Error events
    finally:
        # Clear runtime override
        self.event_manager.set_event_query(None)
```

## Remaining Work

### High Priority
1. ✅ ~~Core EventQuery implementation~~
2. ✅ ~~ScopedContext updates~~
3. ✅ ~~Context builder refactor~~
4. ✅ ~~Actor runtime updates~~
5. ✅ ~~Agent-level `event_query` attribute~~
6. ✅ ~~`event_manager.set_event_query()` method~~
7. ⏳ Update/remove obsolete tests
8. ⏳ Full test suite run

### Testing Strategy
1. Remove Phase 7/8 unit tests (no longer applicable)
2. Update integration tests to use EventQuery
3. Add new integration test for method-scoped filtering
4. Verify 3-level priority system works correctly

## Benefits

### Type Safety
- ✅ EventQuery validated at construction
- ✅ IDE autocomplete for filtering options
- ✅ No string eval or dynamic expressions

### Clarity
- ✅ Events are filtered, not injected
- ✅ Consistent with event_manager.filter() API
- ✅ Clear precedence rules

### Performance
- ✅ EventQuery.apply() is O(n) filtering
- ✅ No DynamicContext resolution overhead
- ✅ Efficient call_id lookups

## Migration Guide

### Old Code (Dict-based)
```python
# THIS NO LONGER WORKS
@strategy(context=ScopedContext(
    events={"reminder": "Focus on security"}
))
```

### New Code (EventQuery)
```python
# Use EventQuery for filtering actual events
@strategy(context=ScopedContext(
    events=EventQuery.current_call()
))

# Static reminders should go in context blocks
@strategy(context=ScopedContext(
    context={"reminder": "Focus on security"}
))
```

## Design Decisions

### Why EventQuery Instead of DynamicContext?
1. **Type safety**: Parameters validated at construction
2. **Performance**: No string evaluation
3. **API consistency**: Matches event_manager.filter()
4. **Clarity**: Explicit filtering vs implicit injection

### Why Child Replaces Parent (Not Merge)?
- EventQuery is a complete filter specification
- Merging would be ambiguous (what does merge of limits mean?)
- Simpler semantics: most specific wins

### Why 4-Level Priority?
1. **Runtime**: Dynamic override via event_manager.set_event_query() - overrides everything
2. **Scoped**: Temporary override within with block
3. **Decorator**: Method-level default
4. **Agent**: Global default
5. **None**: Show all (system default)

This matches the mental model of increasing specificity, with runtime providing the ultimate override for dynamic control.

## Files Modified

1. `src/agent006/runtime/event_query.py` - New file
2. `packages/context-blocks/src/context_blocks/scoped.py` - Updated
3. `src/agent006/runtime/context_builder.py` - Major refactor
4. `src/agent006/runtime/actor.py` - Updated
5. `src/agent006/runtime/event_manager.py` - Added set_event_query() and get_event_query()
6. `src/agent006/agent.py` - Added event_query parameter to Agent class
7. `src/agent006/decorators.py` - Updated examples
8. `src/agent006/__init__.py` - Export EventQuery
9. `src/agent006/runtime/__init__.py` - Export EventQuery

## Breaking Changes

⚠️ **API Change**: `ScopedContext(events={...})` with dict no longer supported.

**Migration**: Use `EventQuery` for filtering, or move static content to `context` blocks.

---

**Next Steps**: Complete remaining tasks (agent-level support, tests) and merge!
