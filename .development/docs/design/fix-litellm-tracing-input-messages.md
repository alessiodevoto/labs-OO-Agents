# MR Design: Fix LiteLLM Tracing Input Message Content

## Summary

Fix the OpenInference LiteLLM instrumentation to properly capture input message content in trace spans. Currently, `llm.input_messages.*.message.content` appears empty in traces even when messages have content.

## Problem Statement

When viewing traces for LLM calls, the input messages show empty content:
```json
{
  "llm.input_messages.0.message.role": "system",
  "llm.input_messages.0.message.content": "",  // Should have content!
  "llm.input_messages.1.message.role": "user",
  "llm.input_messages.1.message.content": ""   // Should have content!
}
```

This makes debugging agent behavior difficult because we can't see what prompts were sent to the LLM.

## Root Cause Analysis

The `openinference-instrumentation-litellm` package uses a helper function `_set_span_attribute` that filters out empty strings:

```python
# In openinference/instrumentation/litellm/__init__.py
def _set_span_attribute(span, name, value):
    if value is not None and value != "":  # <-- Filters empty strings!
        span.set_attribute(name, value)
```

This helper is called for all message content attributes. When content is an empty string `""`, the attribute is silently not set. However, the issue we're seeing is that **non-empty content is also appearing as empty**, which suggests the content extraction logic itself may be failing.

## Investigation Findings

### Existing Patches

We already have patches in `_litellm_patch.py` that fix:
1. **Output message value** - Fixed null/empty content handling for `SpanAttributes.OUTPUT_VALUE`
2. **Tool call IDs** - Added missing `tool_call.id` capture

### Missing Patch

The **input message content** is not patched. The flow for input messages is:

1. `_set_input` is called with the request kwargs
2. It extracts messages and calls `_get_attributes_from_message_param` for each
3. `_get_attributes_from_message_param` yields `(attribute_name, value)` tuples
4. Each tuple is passed to `_set_span_attribute` which filters empty values

The bug likely occurs in step 3, where `_get_attributes_from_message_param` may be returning empty content for certain message formats.

## Proposed Solution

### Approach: Extend Existing Patch Module

Add a third patch to `_litellm_patch.py` that fixes input message content extraction.

### Design

```
┌─────────────────────────────────────────────────────────────────┐
│                    _litellm_patch.py                            │
├─────────────────────────────────────────────────────────────────┤
│ Existing Patches:                                               │
│ ├── _patched_set_output_message_value (output content)          │
│ └── _patched_get_attributes_from_message_param (tool_call.id)   │
│                                                                 │
│ New Patch:                                                      │
│ └── _patched_get_attributes_from_message_param (message content)│
│     - Ensure message.content is captured even when empty        │
│     - Handle different message object types (dict, Message)     │
│     - Use direct attribute setting to bypass _set_span_attribute│
└─────────────────────────────────────────────────────────────────┘
```

### Implementation Options

#### Option A: Extend Existing `_patched_get_attributes_from_message_param`

Modify our existing patch function to also fix content extraction:

```python
def _patched_get_attributes_from_message_param(
    message: Mapping[str, Any],
) -> Iterator[tuple[str, AttributeValue]]:
    # Yield original attributes BUT intercept content handling
    for key, value in _original_get_attributes_from_message_param(message):
        # Skip the original content attribute - we'll handle it ourselves
        if key.endswith(".message.content"):
            continue
        yield (key, value)

    # Always yield content, even if empty (use None check, not empty string check)
    if "content" in message:
        content = message.get("content")
        if content is not None:  # Only skip if actually None, not empty string
            yield (MessageAttributes.MESSAGE_CONTENT, content)
```

**Pros**: Single patch point, minimal code
**Cons**: May interfere with complex message formats

#### Option B: Patch `_set_input` Directly

Create a new patch that wraps `_set_input` to ensure content is captured:

```python
def _patched_set_input(span: trace_api.Span, kwargs: Dict[str, Any]) -> None:
    # Call original _set_input for all non-content attributes
    _original_set_input(span, kwargs)

    # Re-set message content directly, bypassing _set_span_attribute filter
    if messages := kwargs.get("messages"):
        for i, message in enumerate(messages):
            if isinstance(message, dict) and "content" in message:
                content = message.get("content", "")
                span.set_attribute(
                    f"llm.input_messages.{i}.message.content",
                    content if content is not None else ""
                )
```

**Pros**: Clean separation, doesn't modify existing patch
**Cons**: More code, duplicate iteration over messages

#### Option C: Patch `_set_span_attribute` Itself (Not Recommended)

Replace the helper to not filter empty strings for message content attributes.

**Pros**: Fixes root cause
**Cons**: May have unintended side effects on other attributes

### Recommended Approach: Option A

Extend the existing `_patched_get_attributes_from_message_param` function since we already patch it for tool_call.id. This keeps all message-related fixes in one place.

## Testing Plan

1. **Unit Test**: Verify patch correctly yields content for various message formats:
   - String content
   - Empty string content
   - None content
   - List content (multimodal)
   - Missing content key

2. **Integration Test**: Run BigCodeBench task and verify traces show full input messages:
   ```bash
   python run_ablation.py --config agent006 --benchmark bigcodebench --task-ids BigCodeBench/19 --limit 1
   ```

3. **Trace Validation**: Inspect trace file and confirm:
   - `llm.input_messages.*.message.content` contains actual prompt text
   - System prompt, user message, and assistant messages all have content

## Files to Modify

| File | Change |
|------|--------|
| `packages/openinference-instrumentation-agent006/src/openinference_instrumentation_agent006/_litellm_patch.py` | Extend `_patched_get_attributes_from_message_param` to fix content extraction |

## Rollout Plan

1. Implement fix in `_litellm_patch.py`
2. Run trace validation test
3. Run full benchmark suite to ensure no regressions
4. Merge to main branch

## Success Criteria

- [ ] Traces show non-empty `llm.input_messages.*.message.content` for all messages
- [ ] Existing patches (output value, tool_call.id) still work
- [ ] No performance regression in benchmark runs

## Future Considerations

- Consider upstreaming this fix to `openinference-instrumentation-litellm`
- The `_set_span_attribute` helper's empty string filtering may be intentional for some attributes - should discuss with OpenInference maintainers
