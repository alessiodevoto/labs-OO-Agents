# NVIDIA API Endpoints Reference

This document describes the two NVIDIA API endpoints available for LLM inference.

## 1. NVIDIA NIM (Public API)

**Endpoint:** `https://integrate.api.nvidia.com/v1`
**API Key:** `NVIDIA_API_KEY` (prefix: `nvapi-...`)
**Models:** 172 open-source and NVIDIA models

### Example Usage
```bash
curl -s "https://integrate.api.nvidia.com/v1/chat/completions" \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen/qwen3-next-80b-a3b-instruct","messages":[{"role":"user","content":"Hello"}]}'
```

### Notable Models
| Model ID | Description |
|----------|-------------|
| `qwen/qwen3-next-80b-a3b-instruct` | Qwen3 80B instruct |
| `qwen/qwen3-coder-480b-a35b-instruct` | Qwen3 Coder 480B |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | NVIDIA Nemotron 49B |
| `nvidia/llama-3.1-nemotron-ultra-253b-v1` | NVIDIA Nemotron Ultra 253B |
| `deepseek-ai/deepseek-r1` | DeepSeek R1 |
| `deepseek-ai/deepseek-v3.1` | DeepSeek V3.1 |
| `meta/llama-3.3-70b-instruct` | Llama 3.3 70B |
| `mistralai/mistral-large-3-675b-instruct-2512` | Mistral Large 3 675B |
| `openai/gpt-oss-120b` | OpenAI OSS 120B |
| `openai/gpt-oss-20b` | OpenAI OSS 20B |

---

## 2. NVIDIA Internal API (Azure-backed)

**Endpoint:** `https://inference-api.nvidia.com/v1`
**API Key:** `NVIDIA_INTERNAL_API_KEY` (prefix: `sk-...`)
**Models:** Azure-hosted frontier models (GPT-5, o1, o3, etc.)

### Example Usage
```bash
curl -s "https://inference-api.nvidia.com/v1/chat/completions" \
  -H "Authorization: Bearer $NVIDIA_INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"azure/openai/gpt-5","messages":[{"role":"user","content":"Hello"}]}'
```

### Available Models (tested 2025-12-02)

| Request Model | Actual Model Returned | Notes |
|--------------|----------------------|-------|
| `azure/openai/gpt-5` | `gpt-5-2025-08-07` | GPT-5 |
| `azure/openai/gpt-4o` | `gpt-4o-2024-11-20` | GPT-4o |
| `azure/openai/o1` | `azure/o1-2024-12-17` | o1 reasoning model |
| `azure/openai/o3` | `azure/o3-2025-04-16` | o3 reasoning model |

### Models NOT Available (key restricted)
- `azure/openai/gpt-4o-mini` - access denied
- `azure/openai/gpt-5.1` - returns BadRequestError
- `azure/google/gemini-pro` - access denied
- `azure/anthropic/claude-3` - access denied

---

## Environment Variables

Add to `.env`:
```bash
# NVIDIA NIM (public, open-source models)
NVIDIA_API_KEY=nvapi-...

# NVIDIA Internal (Azure-backed frontier models)
NVIDIA_INTERNAL_API_KEY=sk-...
```

---

## run_ablation.py Provider Support

The ablation runner supports three providers:

| Provider | Endpoint | Key | Example Models |
|----------|----------|-----|----------------|
| `openai` | OpenAI API | `OPENAI_API_KEY` | `gpt-4o-mini`, `gpt-4o` |
| `nvidia` | integrate.api.nvidia.com | `NVIDIA_API_KEY` | `qwen/qwen3-next-80b-a3b-instruct` |
| `nvidia_internal` | inference-api.nvidia.com | `NVIDIA_INTERNAL_API_KEY` | `azure/openai/gpt-5` |

### Usage Examples
```bash
# OpenAI
python run_ablation.py --provider openai --model gpt-4o-mini --benchmark bfcl --limit 10

# NVIDIA NIM
python run_ablation.py --provider nvidia --model qwen/qwen3-next-80b-a3b-instruct --benchmark bfcl --limit 10

# NVIDIA Internal (GPT-5)
python run_ablation.py --provider nvidia_internal --model azure/openai/gpt-5 --benchmark bfcl --limit 10
```
