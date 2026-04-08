# OpenAI API Support Fix

## Problem

The nemo_oo_agents-src framework was hardcoded to only use NVIDIA's API, even when users specified OpenAI or Anthropic models. This caused 404 errors when trying to use models like `gpt-4o` because the system was sending the requests to NVIDIA's API endpoint instead of OpenAI's.

## Root Cause

Two files had hardcoded NVIDIA configuration:

1. **`runtime/actor.py` line 153**: Only checked for `NVIDIA_API_KEY`
2. **`runtime/actor.py` line 159**: Always set `api_base = "https://integrate.api.nvidia.com/v1"`
3. **`llm/client.py` line 48**: Default constructor only looked for `NVIDIA_API_KEY`

## Solution

Updated both files to automatically detect which API to use based on the model name prefix:

### Model Prefix Detection

```python
if model.startswith("gpt-") or model.startswith("o1-"):
    # OpenAI models → use OPENAI_API_KEY, default base URL
    api_key = os.getenv("OPENAI_API_KEY")
    api_base = None  # LiteLLM uses default OpenAI base

elif model.startswith("claude-"):
    # Anthropic models → use ANTHROPIC_API_KEY, default base URL
    api_key = os.getenv("ANTHROPIC_API_KEY")
    api_base = None

elif model.startswith("nvidia_nim/"):
    # NVIDIA NIM models → use NVIDIA_API_KEY, NVIDIA base URL
    api_key = os.getenv("NVIDIA_API_KEY")
    api_base = "https://integrate.api.nvidia.com/v1"

else:
    # Default to NVIDIA for backward compatibility
    api_key = os.getenv("NVIDIA_API_KEY")
    api_base = "https://integrate.api.nvidia.com/v1"
```

## Valid Model Names

### OpenAI (requires OPENAI_API_KEY)
- `"gpt-4o"` - Most capable OpenAI model (GPT-4 Optimized)
- `"gpt-4-turbo"` - GPT-4 Turbo
- `"gpt-4"` - Standard GPT-4
- `"gpt-3.5-turbo"` - Fastest, cheapest OpenAI model
- `"o1-preview"` - O1 reasoning model (if you have access)
- `"o1-mini"` - Smaller O1 model (if you have access)

**Note**: There is no `"gpt-4o-mini"` model. Use `"gpt-4o"` or `"gpt-3.5-turbo"` instead.

### Anthropic (requires ANTHROPIC_API_KEY)
- `"claude-3-5-sonnet-20241022"` - Most capable Anthropic model
- `"claude-3-opus-20240229"` - Previous generation flagship
- `"claude-3-sonnet-20240229"` - Balanced model
- `"claude-3-haiku-20240307"` - Fastest, cheapest Anthropic model

### NVIDIA NIM (requires NVIDIA_API_KEY)
- `"nvidia_nim/qwen/qwen3-next-80b-a3b-instruct"` - Recommended
- `"nvidia_nim/qwen/qwen3-coder-480b-a35b-instruct"` - Large, powerful
- `"nvidia_nim/qwen/qwen2.5-coder-32b-instruct"` - Fast, good for code
- `"nvidia_nim/meta/llama-3.3-70b-instruct"` - Larger
- `"nvidia_nim/meta/llama-3.1-8b-instruct"` - Smallest, fastest

## Usage

### 1. Set up your `.env` file

Create or update `/Users/rcabral/agent-proto-006/nemo_oo_agents-src/playground/.env`:

```bash
# Add whichever keys you have:
NVIDIA_API_KEY=your-nvidia-key-here
OPENAI_API_KEY=your-openai-key-here
ANTHROPIC_API_KEY=your-anthropic-key-here
```

### 2. In your notebook, set the MODEL variable

```python
# Choose ONE of these:
MODEL = "gpt-4o"              # OpenAI
MODEL = "gpt-3.5-turbo"       # OpenAI (cheaper)
MODEL = "claude-3-5-sonnet-20241022"  # Anthropic
MODEL = "nvidia_nim/qwen/qwen3-next-80b-a3b-instruct"  # NVIDIA
```

### 3. Re-run the setup cell

After changing the MODEL, re-run the setup cell. The framework will now:
1. Check if you have the appropriate API key for that model
2. Automatically route the request to the correct API endpoint
3. Use the correct authentication

## Files Changed

- `/Users/rcabral/agent-proto-006/nemo_oo_agents-src/src/nemo_oo_agents/runtime/actor.py` - Lines 131-189 (auto-detect API in runtime)
- `/Users/rcabral/agent-proto-006/nemo_oo_agents-src/src/nemo_oo_agents/llm/client.py` - Lines 39-63 (auto-detect API in client)
- `/Users/rcabral/agent-proto-006/nemo_oo_agents-src/src/nemo_oo_agents/llm/model_tester.py` - Lines 10-89 (auto-detect API in test utility)
- `/Users/rcabral/agent-proto-006/nemo_oo_agents-src/playground/agent_experiments.ipynb` - Cell 2 (API key verification)

## Testing

To verify your setup works:

```python
# In notebook cell:
await test_current_model(MODEL)
```

This will:
- Check if you have the required API key
- Test a simple completion
- Suggest alternatives if it fails

## Backward Compatibility

- Existing code using NVIDIA models continues to work unchanged
- Models without a prefix default to NVIDIA for backward compatibility
- No changes needed to existing agent definitions
