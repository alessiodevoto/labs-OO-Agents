# Configuring LiteLLM for NVIDIA NIMs

This guide explains how to configure LiteLLM to call NVIDIA NIM (NVIDIA Inference Microservices) models, based on the configuration patterns used in Nemo Synapse's chat_cli.

## Quick Start

### 1. Set Your API Key

```bash
export NVIDIA_API_KEY="nvapi-your-key-here"
```

Get your free API key from [NVIDIA NGC](https://build.nvidia.com/).

### 2. Basic LiteLLM Call

```python
from litellm import acompletion

response = await acompletion(
    model="nvidia_nim/meta/llama-3.1-8b-instruct",
    messages=[{"role": "user", "content": "Hello!"}],
    api_base="https://integrate.api.nvidia.com/v1",
    temperature=0.7,
    max_tokens=2000
)
```

## Configuration Details

### Model Format

NVIDIA NIM models use the `nvidia_nim/` prefix:

```python
# Format: nvidia_nim/{organization}/{model-name}
model = "nvidia_nim/meta/llama-3.1-8b-instruct"
model = "nvidia_nim/meta/llama-4-maverick-17b-128e-instruct"
model = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1.5"
model = "nvidia_nim/qwen/qwen3-next-80b-a3b-thinking"
```

### Essential Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `model` | `nvidia_nim/{org}/{model}` | Model identifier with prefix |
| `api_base` | `https://integrate.api.nvidia.com/v1` | NVIDIA API endpoint |
| `temperature` | `0.5` - `0.7` | Recommended range for reasoning models |
| `max_tokens` | `2000+` | Many NIM models support large contexts |

### Environment Variables

LiteLLM automatically reads `NVIDIA_API_KEY` from the environment:

```bash
# Option 1: Export in shell
export NVIDIA_API_KEY="nvapi-..."

# Option 2: Use in Python
import os
os.environ["NVIDIA_API_KEY"] = "nvapi-..."
```

### With Fallbacks

Configure fallback models for high availability:

```python
response = await acompletion(
    model="nvidia_nim/meta/llama-3.1-70b-instruct",
    messages=messages,
    api_base="https://integrate.api.nvidia.com/v1",
    fallbacks=[
        "nvidia_nim/meta/llama-3.1-8b-instruct",  # Smaller NIM model
        "gpt-3.5-turbo"  # OpenAI fallback (requires OPENAI_API_KEY)
    ]
)
```

## Complete Example

```python
import asyncio
from litellm import acompletion

async def chat_with_nvidia_nim():
    """Example chat completion with NVIDIA NIM via LiteLLM."""

    response = await acompletion(
        # Model configuration
        model="nvidia_nim/meta/llama-3.1-8b-instruct",
        api_base="https://integrate.api.nvidia.com/v1",

        # Messages
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is 15 * 23 + 7?"}
        ],

        # Generation parameters
        temperature=0.7,
        max_tokens=2000,

        # Optional: Fallback models
        fallbacks=["nvidia_nim/meta/llama-3.1-70b-instruct"],

        # Optional: Request settings
        timeout=30.0,
        max_retries=3
    )

    print(response.choices[0].message.content)
    return response

# Run it
asyncio.run(chat_with_nvidia_nim())
```

## Reasoning Models

For reasoning-capable models (e.g., models with "thinking" in the name), use longer timeouts:

```python
response = await acompletion(
    model="nvidia_nim/qwen/qwen3-next-80b-a3b-thinking",
    messages=messages,
    api_base="https://integrate.api.nvidia.com/v1",
    temperature=0.5,
    max_tokens=20000,  # Reasoning models may need more tokens
    timeout=120.0,     # Extended timeout for reasoning
)
```

## Troubleshooting

### Check API Key
```bash
curl -H "Authorization: Bearer $NVIDIA_API_KEY" \
     https://integrate.api.nvidia.com/v1/models
```

### Common Issues

**Authentication Error**: Verify your `NVIDIA_API_KEY` is set correctly and has not expired.

**Model Not Found**: Check the [NVIDIA NIM catalog](https://build.nvidia.com/) for available models. Model names change over time.

**Timeout**: Increase the `timeout` parameter for larger models or complex requests.

**Rate Limits**: NVIDIA's free tier has rate limits. Consider upgrading or implementing retry logic.

## Multi-Provider Setup

Mix NVIDIA NIM with other providers:

```python
import os

# Set up multiple API keys
os.environ["NVIDIA_API_KEY"] = "nvapi-..."
os.environ["OPENAI_API_KEY"] = "sk-..."
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

# Use with automatic failover
response = await acompletion(
    model="nvidia_nim/meta/llama-3.1-8b-instruct",
    messages=messages,
    api_base="https://integrate.api.nvidia.com/v1",
    fallbacks=[
        "gpt-4o-mini",                    # OpenAI
        "claude-3-haiku-20240307"         # Anthropic
    ]
)
```

## Additional Resources

- [LiteLLM Documentation](https://docs.litellm.ai/)
- [NVIDIA NIM Documentation](https://docs.nvidia.com/nim/)
- [NVIDIA Build Platform](https://build.nvidia.com/)
- [Available NIM Models](https://build.nvidia.com/explore/discover)

## Key Differences from OpenAI

| Aspect | OpenAI | NVIDIA NIM |
|--------|--------|-----------|
| Model prefix | None or `openai/` | `nvidia_nim/` |
| API base | Default (api.openai.com) | Must specify explicitly |
| API key env | `OPENAI_API_KEY` | `NVIDIA_API_KEY` |
| Free tier | No | Yes (with limits) |
