# LiteLLM Bug: NVIDIA NIM Reasoning Models Return `content: null` with Response in `reasoning_content`

## Summary

When using NVIDIA NIM reasoning models through LiteLLM, the model's actual response ends up in the `reasoning_content` field instead of `content`. LiteLLM does not map this field back to `content`, causing downstream code to receive empty/null responses.

## Environment

- **LiteLLM version:** 1.79.1
- **Python:** 3.12
- **Provider:** NVIDIA NIM (`https://inference-api.nvidia.com/v1`)
- **Model:** `nvidia/nvidia/nemotron-nano-31b-v3`

## Minimal Reproduction

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://inference-api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_INFERENCE_API_KEY"],
)

response = client.chat.completions.create(
    model="nvidia/nvidia/nemotron-nano-31b-v3",
    messages=[
        {"role": "system", "content": 'Respond with JSON: {"value": "positive" or "negative"}'},
        {"role": "user", "content": "I love this product!"},
    ],
    max_tokens=100,
)

msg = response.choices[0].message
print(f"content: {msg.content!r}")  # None
print(f"reasoning_content: {getattr(msg, 'reasoning_content', None)!r}")  # Has the response!
```

## Actual Response

```json
{
  "id": "chatcmpl-...",
  "model": "nvidia/nvidia/nemotron-nano-31b-v3",
  "choices": [{
    "finish_reason": "length",
    "index": 0,
    "message": {
      "content": null,
      "role": "assistant",
      "reasoning_content": "Okay, let's see. The user wants me to classify the sentiment...",
      "provider_specific_fields": {
        "reasoning": "Okay, let's see...",
        "reasoning_content": "Okay, let's see..."
      }
    }
  }],
  "usage": {
    "completion_tokens": 100,
    "prompt_tokens": 45,
    "total_tokens": 145
  }
}
```

## Expected Behavior

When a model returns `content: null` but has valid content in `reasoning_content`, LiteLLM should either:

1. **Map `reasoning_content` to `content`** when `content` is null/empty, OR
2. **Document this behavior** for NVIDIA NIM reasoning models so users know to check `reasoning_content`

## Root Cause

NVIDIA NIM reasoning models (like `nemotron-nano-31b-v3`) partition their output:
1. **Thinking/reasoning** goes to `reasoning_content`
2. **Final answer** should go to `content`

However, when `max_tokens` is exhausted during the reasoning phase, the model never writes to `content`. LiteLLM passes through the null `content` without checking if `reasoning_content` has usable data.

## Impact

- **Structured output parsing fails** - Code expecting JSON in `content` gets null
- **Agent frameworks break** - LangChain, CrewAI, etc. expect responses in `content`
- **Silent failures** - No error thrown, just empty responses

## Suggested Fix

In LiteLLM's NVIDIA NIM provider handling, add a fallback:

```python
content = response.choices[0].message.content
if content is None or content == "":
    # Fallback to reasoning_content for reasoning models
    content = getattr(response.choices[0].message, "reasoning_content", None)
```

Or alternatively, expose this as a provider-specific behavior that users can configure.

## Affected Models

Confirmed with:
- `nvidia/nvidia/nemotron-nano-31b-v3`

Likely also affects other NVIDIA NIM reasoning models:
- `nvidia/nvidia/Nemotron-3-Nano-30B-A3B`
- Other models with `reasoning_content` output

## Workaround

Until fixed, users can manually check for `reasoning_content`:

```python
msg = response.choices[0].message
content = msg.content or getattr(msg, "reasoning_content", "") or ""
```
