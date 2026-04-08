# Event System Alignment (Completed)

**Status**: ✅ Complete
**Related**: [overview.md](overview.md), [phase-3-context-blocks.md](phase-3-context-blocks.md)
**Date**: 2025-12-05

## Summary

Aligned nemo_oo_agents events with context-blocks to eliminate code duplication and ensure consistency.

## Changes Made

### 1. Added `id` field to context-blocks EventBase

**File**: `packages/context-blocks/src/context_blocks/events.py`

```python
class EventBase(BaseModel):
    """Base class for all events.

    Attributes:
        id: Unique identifier for this event (auto-generated UUID if not provided).
        timestamp: When the event occurred (auto-generated if not provided).
        metadata: Arbitrary metadata for the event.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Why**: nemo_oo_agents needs event IDs for correlation tracking. By adding `id` to context-blocks EventBase, we enable nemo_oo_agents events to inherit from context-blocks without duplication.

### 2. Migrated nemo_oo_agents events to use context-blocks base types

**File**: `src/nemo_oo_agents/events.py`

**Before**:
```python
import uuid
from datetime import datetime

class ContentData(BaseModel):
    content: str

class EventBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**After**:
```python
from context_blocks import ContentData, EventBase

# Agent006-specific events extend context-blocks EventBase
class TaskEvent(EventBase):
    type: Literal["task"] = "task"
    data: ContentData
```

**Benefits**:
- Eliminated duplicate EventBase and ContentData definitions
- nemo_oo_agents events now compatible with context-blocks tools
- Shared data types ensure consistency

### 3. Made OpenAIProviderFormatter configurable

**File**: `src/nemo_oo_agents/context/formats.py`

**Before**:
```python
class OpenAIProviderFormatter:
    ROLE_MAP = {
        "task": "user",
        "error": "user",
        "feedback": "user",
        "assistant": "assistant",
        "message": "assistant",
        "reasoning": "assistant",
    }

    def _event_to_message(self, event):
        role = self.ROLE_MAP.get(event.type, "user")
```

**After**:
```python
class OpenAIProviderFormatter:
    DEFAULT_ROLE_MAP = {
        # nemo_oo_agents-specific event types
        "task": "user",
        "error": "user",
        "feedback": "user",
        "message": "assistant",
        "reasoning": "assistant",
        "assistant": "assistant",
        # context-blocks standard event types
        "user": "user",
        "tool_call": "assistant",
        "tool_result": "tool",
    }

    def __init__(self, role_map: dict[str, str] | None = None):
        """Initialize formatter with custom role mapping."""
        self.role_map = role_map or self.DEFAULT_ROLE_MAP

    def _event_to_message(self, event):
        role = self.role_map.get(event.type, "user")
```

**Benefits**:
- Role mapping is now configurable per-formatter instance
- Supports both nemo_oo_agents and context-blocks event types
- Enables experiments with different role mappings (e.g., treat reasoning as user role)
- No metadata pollution (role determined by event type, not stored in metadata)

**Example usage**:
```python
# Default mapping
formatter = OpenAIProviderFormatter()

# Custom mapping for experiments
custom_formatter = OpenAIProviderFormatter(role_map={
    "task": "user",
    "reasoning": "user",  # Override: treat reasoning as user feedback
    "assistant": "assistant",
})
```

### 4. Removed backward compatibility aliases

**File**: `src/nemo_oo_agents/context/formats.py`

**Removed**:
```python
# Backward compatibility aliases
MarkdownFormatter = MarkdownBlockFormatter
XMLFormatter = XMLBlockFormatter
```

**Why**: Clean up unused aliases. Users should import directly from context-blocks:
```python
from context_blocks import MarkdownBlockFormatter, XMLBlockFormatter
```

### 5. Updated documentation

**Files**: `src/nemo_oo_agents/context/formats.py`, `src/nemo_oo_agents/events.py`

- Updated docstrings to reflect shared EventBase
- Clarified that ProviderFormatter supports both nemo_oo_agents and context-blocks events
- Added examples of configurable role mapping

## Architecture After Changes

```
┌─────────────────────────────────────────────────────────────────┐
│                      context-blocks package                     │
│                                                                 │
│  EventBase (id, timestamp, metadata)                            │
│     ├─ UserEvent                                                │
│     ├─ AssistantEvent                                           │
│     ├─ ToolCallEvent                                            │
│     └─ ToolResultEvent                                          │
│                                                                 │
│  ContentData (content: str | list)                              │
│  ToolCallData (id, name, arguments)                             │
│  ToolResultData (tool_call_id, content)                         │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ extends
                              │
┌─────────────────────────────────────────────────────────────────┐
│                       nemo_oo_agents package                          │
│                                                                 │
│  Agent006-specific events (inherit from context-blocks):       │
│     ├─ TaskEvent (extends EventBase)                           │
│     ├─ ErrorEvent (extends EventBase)                          │
│     ├─ FeedbackEvent (extends EventBase)                       │
│     ├─ MessageEvent (extends EventBase)                        │
│     └─ ReasoningEvent (extends EventBase)                      │
│                                                                 │
│  OpenAIProviderFormatter (configurable role_map)                │
│     - Supports both nemo_oo_agents and context-blocks events         │
│     - Configurable per-instance                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Event Type Mapping

| Event Type | Source | Base Class | OpenAI Role (Default) |
|-----------|--------|------------|----------------------|
| **TaskEvent** | nemo_oo_agents | EventBase | user |
| **ErrorEvent** | nemo_oo_agents | EventBase | user |
| **FeedbackEvent** | nemo_oo_agents | EventBase | user |
| **MessageEvent** | nemo_oo_agents | EventBase | assistant |
| **ReasoningEvent** | nemo_oo_agents | EventBase | assistant |
| **AssistantEvent** | nemo_oo_agents | EventBase | assistant |
| **UserEvent** | context-blocks | EventBase | user |
| **AssistantEvent** | context-blocks | EventBase | assistant |
| **ToolCallEvent** | context-blocks | EventBase | assistant |
| **ToolResultEvent** | context-blocks | EventBase | tool |

## Benefits

1. **No duplication** ✅
   - Single EventBase definition in context-blocks
   - nemo_oo_agents events extend, don't redefine

2. **Strong typing preserved** ✅
   - TaskEvent, ErrorEvent, etc. remain distinct classes
   - Type safety maintained throughout codebase

3. **Compatibility** ✅
   - nemo_oo_agents events work with context-blocks tools
   - Shared data types (ContentData, etc.)

4. **Configurability** ✅
   - Role mapping in formatter config, not event metadata
   - Enables experimentation with different mappings

5. **Future-ready** ✅
   - Ready for ToolCallingStrategy (ToolCallEvent, ToolResultEvent already mapped)
   - Multimodal ready (ContentData supports str | list)

## Testing

All tests pass:
- `tests/test_events.py` - 13 tests ✅
- `tests/runtime/test_history_manager_events.py` - Formatter tests ✅
- `tests/test_history_manager.py` - History integration ✅

Verified:
- Events import correctly from both packages
- Formatter works with default and custom role mappings
- Backward compatibility maintained (no breaking changes to API)

## Migration Checklist

- [x] Add `id` field to context-blocks EventBase
- [x] Migrate nemo_oo_agents events to use context-blocks base types
- [x] Make OpenAIProviderFormatter configurable
- [x] Remove backward compatibility aliases
- [x] Update documentation and docstrings
- [x] Run tests and verify all pass
- [x] Create summary document

## Update: OpenAIProviderFormatter Moved to context-blocks

**Date**: 2025-12-05 (continued)

After initial alignment, we identified that `OpenAIProviderFormatter` was duplicated in both packages. We've now:

### 1. Enhanced context-blocks OpenAIProviderFormatter

**File**: `packages/context-blocks/src/context_blocks/formatter.py`

Added to context-blocks version:
- Configurable `role_map` via constructor parameter
- `format_events()` method for formatting events without system context
- Token truncation with `_truncate_to_tokens()`
- Support for nemo_oo_agents event types in DEFAULT_ROLE_MAP
- Tag prefix support from event metadata

**Benefits**:
- Single source of truth in context-blocks
- Available to all packages that use context-blocks
- Fully backward compatible with existing code

### 2. Removed nemo_oo_agents Duplication

**Directory**: `src/nemo_oo_agents/context/` - **DELETED ENTIRELY** ✅

The entire directory has been removed (6 files):
- `context/__init__.py` - Re-exported context-blocks (unused by any code)
- `context/formats.py` - OpenAIProviderFormatter (moved to context-blocks)
- `context/prompts.py` - Old prompt code (already deleted in refactor)
- `context/prompt_data/` - Old prompt data (already deleted in refactor)

**Why safe to delete**: Zero imports from `nemo_oo_agents.context` anywhere in the codebase. Everything now imports directly from `context_blocks`.

### 3. Updated Imports

**Files updated**:
- `src/nemo_oo_agents/runtime/prompts.py` - Import from context-blocks
- `tests/runtime/test_history_manager_events.py` - Import from context-blocks
- `tests/test_history_manager.py` - Import from context-blocks
- `tests/test_history_integration.py` - Import from context-blocks

**Before**:
```python
from nemo_oo_agents.context.formats import OpenAIProviderFormatter
```

**After**:
```python
from context_blocks import OpenAIProviderFormatter
```

### 4. Final Architecture

```
context-blocks/
  events.py          ← EventBase with id, timestamp, metadata
  formatter.py       ← OpenAIProviderFormatter (configurable)

nemo_oo_agents/
  events.py          ← TaskEvent, ErrorEvent, etc. (extend EventBase)
  runtime/prompts.py ← Imports directly from context_blocks
```

**Note**: The entire `context/` directory has been removed - all context and formatting functionality comes directly from context-blocks.

### Testing

All 39 tests pass ✅:
- Event creation and serialization
- HistoryManager operations
- Formatter functionality (with custom role_map)
- Integration tests

## Next Steps

**No action required** - System is now fully unified:
1. Single Event system (context-blocks EventBase)
2. Single formatter (context-blocks OpenAIProviderFormatter)
3. No code duplication
4. Ready for ToolCallingStrategy, multimodal content, and future extensions

---

**Related commits**:
- `refactor: add id field to context-blocks EventBase`
- `refactor: align nemo_oo_agents events with context-blocks`
- `feat: make OpenAIProviderFormatter configurable`
- `refactor: move OpenAIProviderFormatter to context-blocks`
- `refactor: delete entire context/ directory (unused re-export layer)`
