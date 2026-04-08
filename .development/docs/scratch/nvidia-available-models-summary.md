# NVIDIA Available Models Summary

**Date**: November 19, 2025
**Status**: Public NIM ✅ | Gateway ✅ **WORKING!**

## Summary

Found **170 models** available on **Public NVIDIA NIM** (integrate.api.nvidia.com).

**Gateway access**: Not working due to OAuth authentication issue. The `NVIDIA_TOKEN_ENDPOINT` in `.env` has a trailing `%` character and credentials may be expired.

## Tested & Working Models

These models were successfully tested on public NIM:

✅ **meta/llama-3.1-8b-instruct** - Fast, small Llama model
✅ **meta/llama-3.3-70b-instruct** - Large, capable Llama model
✅ **qwen/qwen2.5-coder-32b-instruct** - Great for code generation
✅ **qwen/qwen3-coder-480b-a35b-instruct** - Largest Qwen coder
✅ **mistralai/mixtral-8x7b-instruct-v0.1** - MoE model

## Top Models by Category

### Code Generation
- **qwen/qwen3-coder-480b-a35b-instruct** (480B params, active sparsity)
- **qwen/qwen2.5-coder-32b-instruct** (32B params, fast)
- **qwen/qwen2.5-coder-7b-instruct** (7B params, very fast)
- **deepseek-ai/deepseek-coder-6.7b-instruct**
- **nvidia/usdcode-llama-3.1-70b-instruct** (specialized for USD)
- **bigcode/starcoder2-15b**

### General Chat/Reasoning
- **meta/llama-3.3-70b-instruct** (high quality)
- **meta/llama-3.1-405b-instruct** (largest Llama)
- **deepseek-ai/deepseek-r1** (reasoning model)
- **deepseek-ai/deepseek-v3.1** (latest DeepSeek)
- **qwen/qwen3-235b-a22b** (235B with active sparsity)
- **qwen/qwq-32b** (reasoning focus)
- **nvidia/llama-3.3-nemotron-super-49b-v1.5** (NVIDIA tuned)

### Fast/Small Models
- **meta/llama-3.1-8b-instruct** (8B, fast)
- **meta/llama-3.2-3b-instruct** (3B, very fast)
- **meta/llama-3.2-1b-instruct** (1B, extremely fast)
- **google/gemma-2-9b-it** (9B, efficient)
- **google/gemma-3-12b-it** (12B)
- **qwen/qwen2.5-7b-instruct** (7B)
- **nvidia/nemotron-mini-4b-instruct** (4B)

### Multimodal (Vision+Language)
- **meta/llama-3.2-11b-vision-instruct**
- **meta/llama-3.2-90b-vision-instruct**
- **nvidia/vila**
- **nvidia/neva-22b**
- **google/paligemma**

### Embeddings
- **nvidia/nv-embed-v1**
- **nvidia/nv-embedqa-mistral-7b-v2**
- **nvidia/llama-3.2-nv-embedqa-1b-v2**
- **nvidia/nv-embedcode-7b-v1** (code embeddings)
- **baai/bge-m3**
- **snowflake/arctic-embed-l**

### Specialized Models
- **nvidia/nemotron-4-340b-reward** (reward model for RLHF)
- **nvidia/nemoretriever-parse** (document parsing)
- **google/shieldgemma-9b** (safety/moderation)
- **nvidia/riva-translate-4b-instruct** (translation)

## All Available Model Providers

- **meta** (Llama models - 13 variants)
- **qwen** (Qwen models - 11 variants including QwQ reasoning)
- **deepseek-ai** (DeepSeek R1, V3 - 9 variants)
- **nvidia** (Nemotron, embeddings, specialized - 38 models!)
- **google** (Gemma 2, Gemma 3, CodeGemma - 16 variants)
- **mistralai** (Mistral, Mixtral - 10 variants)
- **microsoft** (Phi-3, Phi-4 - 5 variants)
- **01-ai** (Yi models)
- **databricks** (DBRX)
- **writer** (Palmyra specialized models)
- **ai21labs** (Jamba models)
- And 30+ other providers

## Usage Examples

### Using with agent006

```python
from agent006 import Agent
from unifiedllm import UnifiedLLM

# Fast code generation
llm_coder = UnifiedLLM(model="nvidia_nim/qwen/qwen2.5-coder-32b-instruct")
class FastCoder(Agent, llm=llm_coder):
    async def generate_code(self, spec: str):
        """Generate code from specification."""
        ...

# High-quality reasoning
llm_reasoner = UnifiedLLM(model="nvidia_nim/meta/llama-3.3-70b-instruct")
class Reasoner(Agent, llm=llm_reasoner):
    async def analyze(self, problem: str):
        """Analyze problem deeply."""
        ...

# Vision model
llm_vision = UnifiedLLM(model="nvidia_nim/meta/llama-3.2-90b-vision-instruct")
class VisionAgent(Agent, llm=llm_vision):
    async def describe_image(self, image_url: str):
        """Describe what's in an image."""
        ...
```

### Model Selection Guide

**For your use case, choose:**

- **Fast iteration/development**: `qwen/qwen2.5-coder-7b-instruct` or `meta/llama-3.1-8b-instruct`
- **Production code generation**: `qwen/qwen3-coder-480b-a35b-instruct` (current default)
- **General chat/reasoning**: `meta/llama-3.3-70b-instruct` or `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- **Budget/speed priority**: `meta/llama-3.2-3b-instruct` or `nvidia/nemotron-mini-4b-instruct`
- **Vision tasks**: `meta/llama-3.2-90b-vision-instruct`
- **Reasoning tasks**: `deepseek-ai/deepseek-r1` or `qwen/qwq-32b`

## Gateway Access - ✅ WORKING!

**Status**: OAuth authentication is now **fully functional**!

**What was fixed:**
1. ✅ Removed trailing `%` from `NVIDIA_TOKEN_ENDPOINT`
2. ✅ Updated to fresh OAuth credentials
3. ✅ Successfully authenticated and retrieved token
4. ✅ Successfully queried gateway for available models

**Gateway models found (7 total):**

### GPT-5 Models (Latest!)
- **gpt-5** (2025-08-07) - Full GPT-5, 2500 req/min
- **gpt-5-mini** (2025-08-07) - Fast GPT-5, 250 req/min
- **gpt-5-chat** (2025-08-07) - Chat-optimized GPT-5, 250 req/min

### Reasoning Models
- **o3-mini** (2025-01-31) - Latest reasoning, **1M tokens/min**, 103 req/min
- **o3** (2025-04-16) - Full O3 reasoning, 250 req/min

### GPT-4o Models
- **gpt-4o** (2024-11-20) - GPT-4 Omni with JSON Schema, 250 req/10sec
- **gpt-4o-mini** (2024-07-18) - Fast GPT-4o, 2500 req/min

**See detailed documentation**: `docs/scratch/nvidia-gateway-models-working.md`

## Full Model List

See `agent006-src/nvidia_models_available.txt` for complete output with all 170 models.

## Next Steps

1. **Fix OAuth** if you need gateway access (Azure OpenAI/Anthropic models)
2. **Use public NIM** for all the models listed above (already working!)
3. **Update your agents** to use specific models from the list above

## Model Format

When using in agent006, specify the model in the UnifiedLLM client:

```python
from unifiedllm import UnifiedLLM

llm = UnifiedLLM(model="nvidia_nim/provider/model-name")
class MyAgent(Agent, llm=llm):
    ...
```

Examples:
- `nvidia_nim/qwen/qwen2.5-coder-32b-instruct`
- `nvidia_nim/meta/llama-3.3-70b-instruct`
- `nvidia_nim/deepseek-ai/deepseek-r1`
