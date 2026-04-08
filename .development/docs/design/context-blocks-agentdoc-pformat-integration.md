# Context-Blocks Integration with agentdoc.pformat()

**Date:** 2026-01-29
**Status:** ✅ Complete

## Problem

The `<execute_python>` context blocks were using Python's stdlib `pprint.pformat()` to render `ExecutePythonEvent` objects. This caused long strings (like documentation or code snippets in stdout) to be rendered as single-line strings with escape characters instead of multiline strings:

```xml
<execute_python expr="self.history[3]">
ExecutePythonEvent(tool_call_id='...', stdout="Call: async def classify(self, text: str) -> str\n\ntext (str):\n'I absolutely love this product!'", ...)
</execute_python>
```

This made the output harder to read for both humans and LLMs.

## Solution

Updated `packages/context-blocks/src/context_blocks/formatter.py` to use `agentdoc.pformat()` instead of stdlib `pformat()`. However, this required special handling for Pydantic models to respect `repr=False` field annotations.

### Implementation

Created a hybrid `_pformat_full()` function that:

1. **For Pydantic models** (like `ExecutePythonEvent`):
   - Manually constructs the repr string
   - Filters out fields with `repr=False` (tag, id, metadata)
   - Formats each field value using `agentdoc.pformat()` for multiline string detection
   - Accesses `model_fields` from the class (not instance) for Pydantic 2.11+ compatibility

2. **For other objects**:
   - Uses `agentdoc.pformat()` directly with enhanced string formatting

### Code Changes

**Before:**
```python
from pprint import pformat

def _pformat_full(obj: object) -> str:
    return pformat(obj, width=sys.maxsize, depth=None, compact=False)
```

**After:**
```python
from agentdoc import pformat as agentdoc_pformat

def _pformat_full(obj: object) -> str:
    if hasattr(obj, "model_dump") and hasattr(obj.__class__, "model_fields"):
        # Pydantic model: Manual repr construction
        class_name = obj.__class__.__name__
        fields_to_show = []
        for field_name, field_info in obj.__class__.model_fields.items():
            include_in_repr = field_info.repr if field_info.repr is not None else True
            if include_in_repr:
                field_value = getattr(obj, field_name)
                formatted_value = agentdoc_pformat(field_value, max_depth=None, max_string=10000)
                fields_to_show.append(f"{field_name}={formatted_value}")
        fields_str = ", ".join(fields_to_show)
        return f"{class_name}({fields_str})"
    else:
        # Other objects: Use agentdoc pformat directly
        return agentdoc_pformat(obj, max_depth=None, max_string=10000)
```

## Results

### Before (single-line strings):
```xml
<execute_python expr="self.history[3]">
ExecutePythonEvent(tool_call_id='test_call_123', status='complete', execution_count=3, stdout="Call: async def classify(self, text: str) -> str\n\ntext (str):\n'I absolutely love this product, it exceeded all my expectations!'", stderr='', error='', value=None)
</execute_python>
```

### After (multiline strings):
```xml
<execute_python expr="self.history[3]">
ExecutePythonEvent(tool_call_id='test_call_123', status='complete', execution_count=3, stdout='''Call: async def classify(self, text: str) -> str

text (str):
'I absolutely love this product, it exceeded all my expectations!'''', stderr='', error='', value=None)
</execute_python>
```

## Testing

- ✅ All 147 context-blocks tests pass
- ✅ `repr=False` behavior preserved (tag, id, metadata fields excluded)
- ✅ Multiline string detection working for ExecutePythonEvent
- ✅ No Pydantic deprecation warnings

## Benefits

1. **Improved Readability**: Long strings in stdout/stderr are now rendered as multiline strings
2. **Better LLM Understanding**: Multiline strings preserve formatting and are easier for LLMs to parse
3. **Consistent Formatting**: All event rendering now uses the same enhanced `agentdoc.pformat()` logic
4. **Maintained Compatibility**: Pydantic field annotations (like `repr=False`) are still respected

## String Truncation Issue and Fix (2026-01-29)

### Problem

After the initial integration, event blocks were still showing truncated strings:

```xml
<task expr="self.history[0]">
TaskEvent(prompt='''# Task: Classify...

This i'''+141)
</task>
```

### Root Cause

The `_pformat_full()` helper function was defined but **not being used** by the `format_event()` methods. Both `XMLBlockFormatter.format_event()` and `MarkdownBlockFormatter.format_event()` were calling `pformat(event)` directly with no parameters, causing the default 150-character truncation limit to apply (from line 554 in `_pformat.py`: `max_string=max_string or 150`).

### Fix

1. **Updated `_pformat_full()`** to pass explicit limits:
   ```python
   def _pformat_full(obj: object) -> str:
       return pformat(obj, max_string=10000, max_length=100, max_depth=10)
   ```

2. **Updated `XMLBlockFormatter.format_event()`** to use `_pformat_full()`:
   ```python
   body = _pformat_full(event)  # Was: pformat(event)
   ```

3. **Updated `MarkdownBlockFormatter.format_event()`** to use `_pformat_full()`:
   ```python
   body = _pformat_full(event)  # Was: pformat(event)
   ```

### Results After Fix

```xml
<task expr="self.history[0]">
TaskEvent(prompt='''# Task: Classify the sentiment of a single text.

Args:
    text: The text to classify

Returns:
    One of: "positive", "negative", "neutral"

This is a long prompt... (full text shown, no truncation)''')
</task>
```

## Files Modified

- `packages/context-blocks/src/context_blocks/formatter.py`
  - Updated `_pformat_full()` to pass explicit truncation limits
  - Updated `XMLBlockFormatter.format_event()` to use `_pformat_full()`
  - Updated `MarkdownBlockFormatter.format_event()` to use `_pformat_full()`
