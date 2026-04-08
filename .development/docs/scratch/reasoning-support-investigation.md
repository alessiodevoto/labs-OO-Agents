# Reasoning Support Investigation

**Date:** 2026-01-13
**Status:** Investigation Complete

## Summary

This document investigates reasoning support in agent006 and unifiedllm (via LiteLLM) across three areas:
1. How to enable reasoning in LiteLLM
2. Whether it's properly implemented in agent006/unifiedllm
3. Whether reasoning output is captured in traces

## 1. How to Enable Reasoning in LiteLLM

**Critical Finding: LiteLLM does NOT abstract reasoning interfaces.** Each model family requires different handling.

### Model-Specific Reasoning Controls

| Model Family | Enable Reasoning | Disable Reasoning | Output Location |
|--------------|------------------|-------------------|-----------------|
| **OpenAI o1/o3** | `reasoning_effort="medium"` | Don't pass param | `reasoning_content` |
| **DeepSeek** | `thinking={"type": "enabled"}` | `{"type": "disabled"}` | `reasoning_content` |
| **Nemotron** | System prompt: `/think` | `/no_think` | `<think>` tags in content |
| **QwQ/Qwen-reasoning** | Always on | N/A | `<think>` tags in content |

### OpenAI Models (o1/o3-style)
Use `reasoning_effort` parameter:
```python
response = litellm.completion(
    model="openai/o1-mini",
    messages=[{"role": "user", "content": "What is 2+2?"}],
    reasoning_effort="medium",  # "low", "medium", "high"
)
# Reasoning available in: response.choices[0].message.reasoning_content
```

### DeepSeek Reasoner Models
Use `thinking` parameter:
```python
response = litellm.completion(
    model="deepseek/deepseek-reasoner",
    messages=[{"role": "user", "content": "What is 2+2?"}],
    thinking={"type": "enabled"},
)
# Reasoning available in: response.choices[0].message.reasoning_content
```

### NVIDIA Nemotron Models (Nano, Super, etc.)
Use **system prompt** to control reasoning:
```python
# Enable reasoning
messages = [
    {"role": "system", "content": "/think"},  # or "detailed thinking on"
    {"role": "user", "content": "Solve this step by step..."}
]

# Disable reasoning
messages = [
    {"role": "system", "content": "/no_think"},  # or "detailed thinking off"
    {"role": "user", "content": "Quick answer..."}
]
```
- Reasoning output appears in `<think>...</think>` tags within content
- **By default**: Reasoning is typically enabled (models output think tags)
- **Thinking budget**: Some Nemotron models support limiting reasoning tokens

### Models with `<think>` Tags (QwQ, etc.)
These models always embed reasoning in `<think>...</think>` tags within the content. Requires extraction.

## 2. Current Implementation Status

### ✅ What's Implemented

**In `unifiedllm`:**

1. **`LLMResponse.reasoning` field** - Captures reasoning output
   ```python:208:209:packages/unifiedllm/src/unifiedllm/unifiedllm.py
   reasoning: str | None = None  # o1-style or DeepSeek/QwQ reasoning
   ```

2. **`_extract_reasoning_and_usage()`** - Extracts reasoning from LiteLLM responses
   ```python:394:421:packages/unifiedllm/src/unifiedllm/unifiedllm.py
   def _extract_reasoning_and_usage(raw_response: Any) -> tuple[str | None, dict[str, int] | None]:
       """Extract reasoning and usage from raw LLM response."""
       # Extracts from msg.reasoning or msg.reasoning_content
   ```

3. **`ReasoningCompletionClient`** - Specialized client for `<think>` tag models
   - Extracts reasoning from `<think>...</think>` tags
   - Handles LiteLLM bug where opening `<think>` tag is stripped
   - Located at lines 684-781 in `unifiedllm.py`

4. **`_extract_think_tags()`** - Robust extraction for malformed tags
   - Handles both complete and incomplete think tags

5. **`retry_on_empty_content`** - Retry config for reasoning models
   - Some reasoning models return empty content but have reasoning_content

### ❌ Gaps Identified

1. **No automatic `thinking` parameter for DeepSeek models**
   - Users must manually pass `thinking={"type": "enabled"}` via kwargs
   - No model auto-detection to enable reasoning automatically

2. **`reasoning_effort` only used in eval_pipeline**
   - Found in `util/eval_pipeline/` configs and `extra_body` passing
   - Not exposed as first-class parameter in `CompletionClient`
   - Users must pass via `**kwargs` which go to litellm

3. **No model-specific auto-detection**
   - Users must know to use `ReasoningCompletionClient` for think-tag models
   - No automatic client selection based on model name

## 3. Reasoning in Traces

### ❌ NOT Captured

**Critical Gap:** Reasoning content is **NOT** captured in OpenTelemetry traces.

Checked locations:
- `_hooks_impl.py` - Creates spans but doesn't capture `response.reasoning`
- `_litellm_patch.py` - Patches content handling but not reasoning
- Generation hooks capture `result` but not LLM response reasoning

### What's Traced Currently

The following ARE traced:
- Agent method calls (AGENT spans)
- Generation sessions (LLM spans)
- Code execution (TOOL spans)
- Method invocations (TOOL spans)
- Generation results (final output)

### What's Missing in Traces

- `llm.reasoning_content` - The model's reasoning/thinking output
- Token counts for reasoning vs completion (some models report separately)

## Recommendations

### Immediate Fixes (Low effort)

1. **Add reasoning to generation span**
   In `_hooks_impl.py`, modify `after_generation` to capture reasoning:
   ```python
   # In after_generation(), after result capture:
   if hasattr(result, 'reasoning') and result.reasoning:
       span.set_attribute("llm.reasoning_content", result.reasoning[:10000])
   ```

2. **Document kwargs for reasoning models**
   Update examples to show how to enable reasoning:
   ```python
   # For DeepSeek
   response = await client.acall(messages, thinking={"type": "enabled"})

   # For OpenAI o1/o3
   response = await client.acall(messages, reasoning_effort="medium")
   ```

### Medium-term Improvements

3. **Add reasoning params to CompletionClient**
   ```python
   class CompletionClient(UnifiedLLM):
       def __init__(
           self,
           model: str,
           reasoning_effort: str | None = None,  # "low", "medium", "high"
           enable_thinking: bool | None = None,  # For DeepSeek/Nemotron
           **config,
       ):
   ```

4. **Auto-detect reasoning models and apply correct params**
   ```python
   REASONING_MODELS = {
       "deepseek-reasoner": {"param": "thinking", "value": {"type": "enabled"}},
       "o1": {"param": "reasoning_effort", "default": "medium"},
       "o3": {"param": "reasoning_effort", "default": "medium"},
       "nemotron": {"method": "system_prompt", "value": "/think"},
   }
   ```

5. **Add Nemotron system prompt injection**
   For Nemotron models, automatically prepend `/think` or `/no_think` based on config:
   ```python
   if model_is_nemotron(model) and enable_thinking:
       messages = [{"role": "system", "content": "/think"}] + messages
   ```

6. **Instrument LiteLLM for reasoning**
   Patch `openinference-instrumentation-litellm` to capture `reasoning_content` in span attributes.

### For Capability Tests

Until traces capture reasoning, inspect it via:
```python
response = await llm.acall(messages)
if response.reasoning:
    print(f"Reasoning: {response.reasoning}")
```

Or enable debug logging on unifiedllm to see full responses.

## Files Reference

| File | Purpose |
|------|---------|
| `packages/unifiedllm/src/unifiedllm/unifiedllm.py` | LLM client with reasoning extraction |
| `packages/unifiedllm/src/unifiedllm/retry.py` | EmptyContentError for reasoning retry |
| `packages/openinference-instrumentation-agent006/src/openinference_instrumentation_agent006/_hooks_impl.py` | Tracing hooks (missing reasoning) |
| `util/eval_pipeline/src/eval_pipeline/model_factory.py` | reasoning_effort config handling |
