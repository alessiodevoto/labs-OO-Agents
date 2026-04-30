"""Model factory loading from models.yaml.

Usage:
    from eval_pipeline.model_factory import client, client_for

    # Get a configured client by model ID
    llm = client("aws/anthropic/claude-haiku-4-5-v1")

    # Or by default role
    llm = client_for("agent")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from dotenv import find_dotenv, load_dotenv

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import CompletionClient

# Load .env with override=True to ensure .env values take precedence
# over any pre-existing shell environment variables
_env_file = find_dotenv(usecwd=True)
if _env_file:
    load_dotenv(_env_file, override=True)

# Track if error capture has been enabled (only enable once globally)
_error_capture_enabled = False


@lru_cache(maxsize=1)
def _load_models_yaml() -> dict[str, Any]:
    """Load and cache models.yaml from this package."""
    yaml_path = Path(__file__).parent / "models.yaml"
    with open(yaml_path) as f:
        return yaml.safe_load(f)


@dataclass
class ModelConfig:
    """Configuration for a model."""

    model_id: str
    name: str
    model_name: str | None = None  # Actual API model name (if different from model_id)
    endpoint: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    provider: str | None = None
    tags: list[str] | None = None
    reasoning_model: bool = False
    reasoning_effort: str | None = None  # low, medium, high
    max_thinking_tokens: int | None = None  # Cap on reasoning tokens (nvext)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    # Retry configuration
    max_retries: int | None = None  # Max retry attempts (default: 3 if retry enabled)
    retry_on_empty_content: bool = False  # Retry when reasoning models return empty content

    def get_api_key(self) -> str | None:
        """Get API key from environment."""
        return os.getenv(self.api_key_env)


def get(model_id: str) -> ModelConfig:
    """Get model config by model ID.

    Args:
        model_id: Full model ID (e.g., 'nvidia_nim/qwen/qwen3-next-80b-a3b-instruct')

    Returns:
        ModelConfig for the model

    Raises:
        KeyError: If model not found in models.yaml
    """
    data = _load_models_yaml()
    models = data.get("models", {})

    if model_id not in models:
        raise KeyError(
            f"Model '{model_id}' not found in models.yaml. Available: {list(models.keys())}"
        )

    cfg = models[model_id]
    return ModelConfig(
        model_id=model_id,
        name=cfg.get("name", model_id),
        model_name=cfg.get("model_name"),  # Actual API model name
        endpoint=cfg.get("endpoint"),
        api_key_env=cfg.get("api_key_env", "OPENAI_API_KEY"),
        provider=cfg.get("provider"),
        tags=cfg.get("tags"),
        reasoning_model=cfg.get("reasoning_model", False),
        reasoning_effort=cfg.get("reasoning_effort"),
        max_thinking_tokens=cfg.get("max_thinking_tokens"),
        temperature=cfg.get("temperature"),
        max_tokens=cfg.get("max_tokens"),
        top_p=cfg.get("top_p"),
        max_retries=cfg.get("max_retries"),
        retry_on_empty_content=cfg.get("retry_on_empty_content", False),
    )


def get_default(role: str) -> str:
    """Get the default model ID for a role.

    Args:
        role: One of 'playground', 'evaluation', 'agent', 'judge'

    Returns:
        Model ID string
    """
    data = _load_models_yaml()
    defaults = data.get("defaults", {})

    if role not in defaults:
        raise KeyError(f"No default for role '{role}'. Available: {list(defaults.keys())}")

    return defaults[role]


def list_models() -> list[str]:
    """List all available model IDs."""
    data = _load_models_yaml()
    return list(data.get("models", {}).keys())


def client(model_id: str, **kwargs) -> "CompletionClient":
    """Create a configured LLM client for a model ID.

    Tries models.yaml first, then falls back to the unifiedllm registry
    (which supports client_type dispatch for Responses API models).

    Args:
        model_id: Full model ID from models.yaml or unifiedllm registry key
        **kwargs: Override config values (e.g., temperature, max_retries, retry_on_empty_content)

    Returns:
        Configured UnifiedLLM client (CompletionClient or ResponsesClient)
    """
    # Try unifiedllm registry first — it handles client_type dispatch
    try:
        from unifiedllm.registry import MODELS as _REGISTRY, get_llm_client
        if model_id in _REGISTRY:
            return get_llm_client(model_id, **kwargs)
    except ImportError:
        pass
    import litellm

    from nemo_oo_agents.unifiedllm import CompletionClient, RetryConfig

    # Drop unsupported params (e.g., tool_choice for some Azure models via NVIDIA)
    litellm.drop_params = True

    # Enable error capture if CAPTURE_LLM_ERRORS is set (only once globally)
    global _error_capture_enabled
    if not _error_capture_enabled and os.getenv("CAPTURE_LLM_ERRORS"):
        from nemo_oo_agents.unifiedllm.http_logging import enable_http_request_logging

        # Determine output directory - use CAPTURE_LLM_ERRORS value if it's a path
        capture_setting = os.getenv("CAPTURE_LLM_ERRORS")
        if capture_setting and capture_setting.lower() not in ["1", "true", "yes"]:
            output_dir = Path(capture_setting)
        else:
            output_dir = Path("eval_errors")

        enable_http_request_logging(
            output_dir=output_dir,
            errors_only=True,
            save_responses=True,
            verbose=os.getenv("CAPTURE_LLM_ERRORS_VERBOSE", "false").lower()
            in ["1", "true", "yes"],
        )
        _error_capture_enabled = True
        print(f"✓ LLM error capture enabled - writing to: {output_dir / 'llm_errors.jsonl'}")

    cfg = get(model_id)

    # Use model_name if specified, otherwise fall back to model_id
    # The model_name should include the litellm routing prefix (e.g., openai/)
    litellm_model = cfg.model_name or cfg.model_id

    # Build config dict for CompletionClient
    # Use model's max_tokens if specified, otherwise default
    default_max_tokens = cfg.max_tokens or 4096
    config: dict[str, Any] = {
        "api_key": cfg.get_api_key() or "",
        "temperature": kwargs.get("temperature", cfg.temperature or 0.0),
        "max_tokens": kwargs.get("max_tokens", default_max_tokens),
    }

    if cfg.endpoint:
        config["api_base"] = cfg.endpoint

    if cfg.top_p is not None:
        config["top_p"] = kwargs.get("top_p", cfg.top_p)

    # Add reasoning_effort and max_thinking_tokens for reasoning models
    if cfg.reasoning_effort or cfg.max_thinking_tokens:
        extra_body: dict[str, Any] = {}
        if cfg.reasoning_effort:
            extra_body["reasoning_effort"] = cfg.reasoning_effort
        if cfg.max_thinking_tokens:
            extra_body["nvext"] = {"max_thinking_tokens": cfg.max_thinking_tokens}
        config["extra_body"] = extra_body

    # Build retry config if any retry options specified
    max_retries = kwargs.get("max_retries", cfg.max_retries)
    retry_on_empty_content = kwargs.get("retry_on_empty_content", cfg.retry_on_empty_content)

    if max_retries is not None or retry_on_empty_content:
        config["retry_config"] = RetryConfig(
            max_retries=max_retries if max_retries is not None else 3,
            retry_on_empty_content=retry_on_empty_content,
        )

    # Check if registry indicates this should use ResponsesClient
    from unifiedllm.registry import MODELS as _REG_MODELS
    _reg_cfg = _REG_MODELS.get(model_id, {})
    if _reg_cfg.get("client_type") == "responses":
        from unifiedllm import ResponsesClient
        return ResponsesClient(model=litellm_model, **config)
    return CompletionClient(model=litellm_model, **config)


def client_for(role: str, **kwargs) -> CompletionClient:
    """Create a client for the default model of a role.

    Args:
        role: One of 'playground', 'evaluation', 'agent', 'judge'
        **kwargs: Override config values

    Returns:
        Configured CompletionClient from unifiedllm
    """
    model_id = get_default(role)
    return client(model_id, **kwargs)
