# Bug: openinference-instrumentation-litellm logs entire response as JSON when content is empty string

## Summary

`openinference-instrumentation-litellm` has a bug where it logs the entire API response as JSON in `output.value` when `message.content` is an empty string `""`. This happens because empty string is falsy in Python, causing the content check to fail and fall through to a fallback that dumps the entire response.

## Affected Version

- `openinference-instrumentation-litellm` v0.1.28

## Environment

- Python 3.12
- litellm 1.79.1
- Model: `openai/gpt-oss-20b` via NVIDIA NIM API (but affects any model that returns empty content)

## Steps to Reproduce

1. Make a litellm call to a model that returns empty `content` but has other fields (e.g., `reasoning_content`)
2. Observe that `output.value` in the OTel span contains the entire API response as JSON instead of the empty content

This is non-deterministic with gpt-oss-20b but the bug in the instrumentation code is deterministic.

## Root Cause

In `/openinference/instrumentation/litellm/__init__.py` lines 111-120:

```python
def _set_output_message_value(span: trace_api.Span, result: ModelResponse) -> Any:
    if (
        result.choices
        and isinstance(result.choices[-1], Choices)
        and (output_value := result.choices[-1].message.content)  # BUG: empty string is falsy!
    ):
        _set_span_attribute(span, SpanAttributes.OUTPUT_VALUE, output_value)
    else:
        # Falls through when content is empty string ""
        _set_span_attribute(span, SpanAttributes.OUTPUT_VALUE, result.model_dump_json())
        _set_span_attribute(
            span, SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.JSON.value
        )
```

**The Problem:**
- The walrus operator `(output_value := result.choices[-1].message.content)` assigns the value
- But when `content` is `""` (empty string), it's falsy
- The `and` short-circuits, skipping the `if` block
- Falls through to `else` which logs `result.model_dump_json()` - the **entire response**

## Expected Behavior

When `message.content` is an empty string `""`:
- `output.value` should be `""`
- Should NOT log the entire response as JSON

## Actual Behavior

When `message.content` is `""`:
- `output.value` contains the entire API response as a JSON string:
```json
{"id":"chatcmpl-xxx","created":123,"model":"openai/gpt-oss-20b","choices":[{"finish_reason":"stop","message":{"content":"","reasoning_content":"..."}}],...}
```

## Impact

1. **Debugging is harder** - The trace shows a massive JSON blob instead of the actual (empty) content
2. **Trace file size increases** - Entire response is duplicated in output.value
3. **Misleading telemetry** - Makes it look like the response was malformed when it was actually a valid empty response

## Proposed Fix

Change the condition to explicitly check for `None` instead of relying on truthiness:

```python
def _set_output_message_value(span: trace_api.Span, result: ModelResponse) -> Any:
    if (
        result.choices
        and isinstance(result.choices[-1], Choices)
        and (output_value := result.choices[-1].message.content) is not None  # Fix: check for None
    ):
        _set_span_attribute(span, SpanAttributes.OUTPUT_VALUE, output_value)
    else:
        _set_span_attribute(span, SpanAttributes.OUTPUT_VALUE, result.model_dump_json())
        _set_span_attribute(
            span, SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.JSON.value
        )
```

## Evidence

Trace file showing the bug:
- File: `SentimentAgent_gpt-oss-20b_classify_batch_20251212_161642_857053.006trace.jsonl`
- Span 6 shows:
  - `llm.output_messages.0.message.content`: `""`
  - `output.value`: `{"id":"chatcmpl-bb6ddfe184ca45a8b800164327d84eda","created":1765552608,"model":"openai/gpt-oss-20b",...}` (entire response)

## Related Issues

This was discovered while investigating why gpt-oss-20b sometimes returns empty content with only `reasoning_content` populated. That's a separate model behavior issue, but this instrumentation bug made it much harder to diagnose.

## Labels

- bug
- openinference
- litellm
- telemetry
