# GPT-OSS-20B Reasoning Content Bug - Root Cause Analysis

**Date**: 2025-12-12
**Issue**: gpt-oss-20b outputs reasoning but no code, causing "Empty response" errors

## Problem Summary

When using the `openai/gpt-oss-20b` model with the PURE_PYTHON strategy, the model generates reasoning content but returns empty actual content, causing the agent to fail with "Empty response" errors.

## Example Trace

File: `/Volumes/dev/dev/agent006/util/e2e_optimization/experiments/capability_20251212_161605/traces/SentimentAgent_gpt-oss-20b_classify_batch_20251212_161642_857053.006trace.jsonl`

**LLM Response** (span 6):
```json
{
  "id": "chatcmpl-bb6ddfe184ca45a8b800164327d84eda",
  "model": "openai/gpt-oss-20b",
  "choices": [{
    "finish_reason": "stop",
    "message": {
      "content": "",  // ❌ EMPTY - no code output
      "role": "assistant",
      "reasoning_content": "We defined a function _classify_batch but didn't call it properly..."  // ✅ HAS REASONING
    }
  }],
  "usage": {
    "completion_tokens": 210,
    "prompt_tokens": 917,
    "total_tokens": 1127
  }
}
```

## Root Cause Analysis

### 1. Model Behavior
- `gpt-oss-20b` is a reasoning model (similar to o1, DeepSeek-R1, QwQ)
- When configured with `reasoning_effort: "medium"`, the model outputs its thinking process in the `reasoning_content` field
- In this case, the model **only** generated reasoning without any code in the `content` field
- `finish_reason` is "stop" (not "length"), so it's not a token limit issue

### 2. UnifiedLLM Handling

**File**: [packages/unifiedllm/src/unifiedllm/unifiedllm.py:356-383](../packages/unifiedllm/src/unifiedllm/unifiedllm.py#L356-L383)

The `_extract_reasoning_and_usage()` function correctly extracts `reasoning_content`:
```python
reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
```

**File**: [packages/unifiedllm/src/unifiedllm/unifiedllm.py:576](../packages/unifiedllm/src/unifiedllm/unifiedllm.py#L576)

But the content extraction just uses the empty string:
```python
text_content = raw_response.choices[0].message.content or ""  # Returns ""
```

This creates an `LLMResponse` with:
- `content = ""`
- `reasoning = "We defined a function..."` (extracted correctly)

### 3. Actor Runtime Handling

**File**: [src/agent006/runtime/actor.py:163](../src/agent006/runtime/actor.py#L163)

The `generate()` method uses only the content field:
```python
content = response.content or ""  # Empty string when reasoning-only response
```

### 4. PURE_PYTHON Strategy Detection

**File**: [src/agent006/strategies/pure_python.py:197-210](../src/agent006/strategies/pure_python.py#L197-L210)

The `_generate_code()` method:
```python
response, event_id = await runtime.generate(tools=[])
code = (response.content or "").strip()  # Empty string!
```

**File**: [src/agent006/strategies/pure_python.py:158-161](../src/agent006/strategies/pure_python.py#L158-L161)

Empty code triggers the error path:
```python
if not code:
    session.record_error()
    await self._send_empty_response_error(runtime, call.method_name)
    continue
```

### 5. Error Message

**File**: [src/agent006/strategies/pure_python.py:315-317](../src/agent006/strategies/pure_python.py#L315-L317)

The error message is generic:
```python
message = f"Empty response. Output Python code. Use `return` to complete {method_name}."
```

This doesn't explain **why** the response was empty (reasoning-only output).

## Why This Happens

The model is following the reasoning model behavior:
1. It generates reasoning about what code to write
2. It outputs that reasoning in `reasoning_content`
3. It **should** then output the actual code in `content`
4. But it's **only** outputting reasoning, not code

This could be due to:
- Prompt engineering issues (model not understanding it needs to output code **after** reasoning)
- Model behavior with `reasoning_effort: "medium"` parameter
- Token limit during reasoning phase (though `finish_reason: "stop"` suggests otherwise)

## Current Flow

```
1. PURE_PYTHON calls runtime.generate()
   ↓
2. runtime.generate() calls llm.acall()
   ↓
3. llm.acall() returns LLMResponse(content="", reasoning="...")
   ↓
4. PURE_PYTHON extracts response.content → ""
   ↓
5. Detects empty code → sends "Empty response" error
   ↓
6. Model tries again (still reasoning-only)
   ↓
7. Fails after max_retries
```

## Reproduction - SUCCESSFUL

### Test Results

| Config | Empty Responses | Pattern |
|--------|-----------------|---------|
| `reasoning_effort=low` | 0/5 | ~37 avg tokens |
| `reasoning_effort=medium` | 0/5 | ~74 avg tokens |
| `reasoning_effort=high` | 0/5 | ~143 avg tokens |
| `reasoning_effort=medium, max_tokens=100` | 0/5 | OK |
| **`reasoning_effort=medium, max_tokens=50`** | **4/5 empty!** | All 50 tokens → reasoning |
| **`no reasoning_effort`** | **1/5 empty** | One used all 500 tokens for reasoning |

### Key Finding

**The bug is reproducible when `max_tokens` is exhausted during reasoning:**

```
max_tokens=50 + reasoning_effort=medium:
  - Model generates reasoning_content: ~180 chars
  - Uses exactly 50 completion tokens (the limit)
  - Has ZERO tokens left for content
  - Returns: content="" + reasoning_content="..."
```

### Root Cause Confirmed

1. gpt-oss-20b partitions output into `reasoning_content` (thinking) and `content` (answer)
2. The `reasoning_effort` parameter sets a SOFT budget for thinking tokens:
   - `low`: ~50 tokens
   - `medium`: ~100 tokens
   - `high`: ~200 tokens
   - `none`: unbounded (uses all available tokens)
3. When `max_tokens` is hit during reasoning, the model stops with empty content
4. The model does NOT reserve tokens for content - it's first-come-first-served

## Proposed Fixes

### Option 1: Use `nvext.max_thinking_tokens` (Recommended)
Explicitly limit thinking tokens to ensure room for content:
```python
extra_body={
    "reasoning_effort": "medium",
    "nvext": {"max_thinking_tokens": 500}  # Cap thinking, leave room for content
}
```

### Option 2: Increase `max_tokens` significantly
Ensure enough headroom for both reasoning AND content:
```python
max_tokens=8192  # Plenty of room for reasoning + content
```

### Option 3: Use Reasoning as Content Fallback
When `content` is empty but `reasoning` exists, use reasoning as content:
```python
# In pure_python.py _generate_code()
code = (response.content or response.reasoning or "").strip()
```

**Pros**: Simple, handles the case immediately
**Cons**: Reasoning is not valid Python code, will cause execution errors

### Option 2: Better Error Message
Detect reasoning-only responses and provide specific feedback:
```python
# In pure_python.py
if not code and response.reasoning:
    message = (
        f"You output reasoning but no code. "
        f"After your thinking, output the actual Python code to execute. "
        f"Use `return` to complete {method_name}."
    )
```

**Pros**: Gives model better guidance to fix the issue
**Cons**: Requires retry, wastes tokens

### Option 3: Prompt Engineering
Update the PURE_PYTHON strategy prompt to explicitly instruct reasoning models:
```
For reasoning models: After your thinking, output the Python code.
Your reasoning will be captured automatically.
```

**Pros**: Prevents the issue proactively
**Cons**: Need to detect reasoning models or apply to all models

### Option 4: Extract Code from Reasoning
Parse the reasoning content to extract code snippets:
```python
# Extract code from reasoning if content is empty
if not code and response.reasoning:
    code = extract_code_from_reasoning(response.reasoning)
```

**Pros**: Works around the model behavior
**Cons**: Fragile, may extract wrong code or incomplete code

## Recommended Solution

**Combination of Option 2 + Option 3**:

1. **Better error message** (immediate fix):
   - Detect when `response.content` is empty but `response.reasoning` exists
   - Provide specific feedback to the model about outputting code after reasoning

2. **Prompt update** (long-term fix):
   - Add guidance to PURE_PYTHON strategy prompt about reasoning models
   - Clarify that code should be in the actual response, not just in thinking

3. **Add reasoning to error context** (debugging aid):
   - When empty response is detected, include reasoning summary in error message
   - Helps model understand what it was thinking and what to do next

## Next Steps

1. ✅ Document root cause
2. ⏳ Implement fix (Option 2 + 3 combination)
3. ⏳ Test with gpt-oss-20b
4. ⏳ Verify with other reasoning models (if available)
5. ⏳ Consider adding reasoning model detection to auto-adjust prompts
