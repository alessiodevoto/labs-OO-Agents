"""Model registry for UnifiedLLM.

Static model registry.
All models route through NVIDIA internal gateway (OpenAI-compatible).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifiedllm import CompletionClient


# NVIDIA internal gateway - all models route through here
NVIDIA_ENDPOINT = "https://inference-api.nvidia.com/v1"
NVIDIA_API_KEY_ENV = "NVIDIA_INTERNAL_API_KEY"

# NVIDIA public NIM endpoint
NVIDIA_PUBLIC_ENDPOINT = "https://integrate.api.nvidia.com/v1"
NVIDIA_PUBLIC_API_KEY_ENV = "NVIDIA_API_KEY"


MODELS: dict[str, dict[str, Any]] = {
    # =========================================================================
    # NVIDIA Nemotron Series
    # =========================================================================
    "nvidia/nvidia/Nemotron-3-Nano-30B-A3B": {
        "context_window": 262_144,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
        "temperature": 0.7,
        "top_p": 0.7,
        "max_tokens": 16384,
    },
    "nvidia/nvidia/nemotron-3-super-preview": {
        "context_window": 262_144,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
        "temperature": 0.7,
        "top_p": 0.7,
        "max_tokens": 16384,
    },
    "nvidia/nvidia/llama-3.3-nemotron-super-49b-v1.5": {
        "context_window": 131_072,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
        "temperature": 0.6,
        "top_p": 0.95,
    },
    "nvidia/nvidia/llama-3.1-nemotron-ultra-253b-v1": {
        "context_window": 131_072,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    # =========================================================================
    # NVIDIA Qwen Series
    # =========================================================================
    "nvidia/qwen/qwen3-next-80b-a3b-instruct": {
        "context_window": 262_144,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "nvidia/qwen/qwen-235b": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "nvidia/qwen/qwen3.5-397b-a17b": {
        "context_window": 131_072,
        "endpoint": NVIDIA_PUBLIC_ENDPOINT,
        "api_key_env": NVIDIA_PUBLIC_API_KEY_ENV,
        "model_name": "qwen/qwen3.5-397b-a17b",
    },
    # =========================================================================
    # NVIDIA GPT-OSS Series
    # =========================================================================
    "nvidia/openai/gpt-oss-20b": {
        "context_window": 131_072,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "nvidia/openai/gpt-oss-120b": {
        "context_window": 131_072,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    # =========================================================================
    # NVIDIA Meta Llama Series
    # =========================================================================
    "nvidia/meta/llama-3.3-70b-instruct": {
        "context_window": 131_072,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "nvidia/meta/llama-3.1-8b-instruct": {
        "context_window": 131_072,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    # =========================================================================
    # Azure OpenAI Models
    # =========================================================================
    "azure/openai/o4-mini": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "azure/openai/gpt-5.2": {
        "context_window": 272_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "azure/openai/gpt-5.1": {
        "context_window": 272_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "azure/openai/gpt-5-mini": {
        "context_window": 272_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    # =========================================================================
    # AWS Anthropic Models
    # =========================================================================
    "aws/anthropic/claude-opus-4-5": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "aws/anthropic/claude-haiku-4-5-v1": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "aws/anthropic/bedrock-claude-sonnet-4-5-v1": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    # Claude reasoning variants (extended thinking)
    "aws/anthropic/claude-haiku-4-5-v1-reasoning-high": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
        "temperature": 1.0,
        "max_tokens": 16384,
    },
    "aws/anthropic/bedrock-claude-sonnet-4-5-v1-reasoning-high": {
        "context_window": 200_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
        "temperature": 1.0,
        "max_tokens": 16384,
    },
    # =========================================================================
    # GCP Google Gemini Models
    # =========================================================================
    "gcp/google/gemini-3-pro": {
        "context_window": 1_000_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "gcp/google/gemini-2.5-flash-lite": {
        "context_window": 1_000_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "gcp/google/gemini-2.5-pro": {
        "context_window": 1_000_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
    "gcp/google/gemini-2.5-flash": {
        "context_window": 1_000_000,
        "endpoint": NVIDIA_ENDPOINT,
        "api_key_env": NVIDIA_API_KEY_ENV,
    },
}


def get_llm_client(name: str, **overrides) -> CompletionClient:
    """Create a CompletionClient from registry config.

    Args:
        name: Model name (e.g., "azure/openai/gpt-5-mini")
        **overrides: Override any parameter (max_tokens, temperature, etc.)

    Returns:
        Configured CompletionClient.

    Note:
        All models are routed through NVIDIA's internal gateway using an
        "openai/" prefix for litellm. This prefix tells litellm to use
        OpenAI-compatible API format. For actual OpenAI models (not through
        NVIDIA gateway), use CompletionClient directly.

    Example:
        llm = get_llm_client("azure/openai/gpt-5-mini")
        llm = get_llm_client("nvidia/meta/llama-3.1-8b-instruct", max_tokens=1000)
    """
    from unifiedllm import CompletionClient

    config = MODELS.get(name, {})

    # Get endpoint and API key
    api_base = config.get("endpoint", NVIDIA_ENDPOINT)
    api_key_env = config.get("api_key_env", NVIDIA_API_KEY_ENV)
    api_key = os.getenv(api_key_env)

    # Build params
    # - openai/ prefix tells litellm to use OpenAI-compatible format
    # - drop_params=True prevents litellm from rejecting tool_choice for azure/* models
    # - model_name override allows registry key to differ from API model name
    #   (e.g. public NIM uses "qwen/qwen3.5-397b-a17b" not "nvidia/qwen/...")
    api_model_name = config.get("model_name", name)
    params: dict = {
        "model": f"openai/{api_model_name}",
        "drop_params": True,
    }

    if api_key:
        params["api_key"] = api_key
    if api_base:
        params["api_base"] = api_base

    # Copy model-specific params from config
    for key in ("temperature", "top_p", "max_tokens"):
        if key in config and key not in overrides:
            params[key] = config[key]

    # Apply user overrides last
    params.update(overrides)

    client = CompletionClient(**params)

    # Attach config for context_window lookup
    client._registry_config = config

    return client
