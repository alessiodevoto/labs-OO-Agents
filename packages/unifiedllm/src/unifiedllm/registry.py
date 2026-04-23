# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""YAML-based model registry for UnifiedLLM.

The registry is a *thin* layer on top of litellm. Most users do not need it:
litellm already knows how to route every common public model (OpenAI, Anthropic,
Google, Azure, etc.) via its built-in model database. Pass the model name
directly to ``get_llm_client()`` and litellm handles the rest.

The registry is useful for *custom* models — models behind a proxy/gateway,
models with non-standard API keys, or convenience aliases for a team.
Define them in YAML and point the framework at them:

Config layering (last wins):
1. ``UNIFIEDLLM_CONFIG`` env var — comma-separated list of YAML paths
2. ``./llm_config.yaml`` in the working directory

YAML schema::

    models:
      my-alias:
        model_name: openai/my-org/my-model   # exact litellm routing string
        api_base: https://my-gateway.example.com/v1
        api_key_env: MY_API_KEY
        context_window: 128000               # optional
        temperature: 0.0                     # optional
        top_p: 1.0                           # optional
        max_tokens: 4096                     # optional
        drop_params: true                    # optional, defaults to true

Set a model to ``null`` in a later layer to remove it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from unifiedllm import CompletionClient

logger = logging.getLogger(__name__)

# Environment variable for extra config file paths (comma-separated)
_CONFIG_ENV_VAR = "UNIFIEDLLM_CONFIG"


def _config_file_chain() -> list[Path]:
    """Return the ordered list of config files to load (first = lowest priority)."""
    files: list[Path] = []

    # 1. Extra configs from UNIFIEDLLM_CONFIG env var
    extra = os.environ.get(_CONFIG_ENV_VAR, "").strip()
    if extra:
        for raw_path in extra.split(","):
            raw_path = raw_path.strip()
            if not raw_path:
                continue
            p = Path(raw_path).expanduser().resolve()
            if p.exists():
                files.append(p)
            else:
                logger.warning("UNIFIEDLLM_CONFIG path does not exist: %s", p)

    # 2. CWD override (highest priority)
    cwd_config = Path.cwd() / "llm_config.yaml"
    if cwd_config.exists():
        files.append(cwd_config)

    return files


def _load_models_from_yaml(path: Path) -> dict[str, dict[str, Any] | None]:
    """Load the 'models' section from a YAML config file."""
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        logger.exception("Failed to load config file: %s", path)
        return {}

    if not isinstance(data, dict):
        return {}

    models = data.get("models")
    if models is None:
        return {}
    if not isinstance(models, dict):
        logger.warning("'models' key is not a mapping in: %s", path)
        return {}

    return models


def _load_registry() -> dict[str, dict[str, Any]]:
    """Load and merge all config layers into the model registry."""
    merged: dict[str, dict[str, Any]] = {}

    for path in _config_file_chain():
        logger.debug("Loading LLM registry config from: %s", path)
        for name, cfg in _load_models_from_yaml(path).items():
            if cfg is None:
                merged.pop(name, None)
            else:
                merged[name] = cfg

    return merged


# MODELS is the merged view of all YAML config layers (UNIFIEDLLM_CONFIG +
# ./llm_config.yaml). Consumers:
#   - get_llm_client() — looks up config when you pass a registered alias
#   - TUI /model and /models commands — list and autocomplete aliases
#   - External tools (eval_pipeline, nat plugin) — resolve alias → endpoint/key
#
# Loaded once at import. Use reload_registry() to refresh after editing
# config files at runtime; it updates this dict in-place so existing
# references stay live.
MODELS: dict[str, dict[str, Any]] = _load_registry()


def reload_registry() -> dict[str, dict[str, Any]]:
    """Reload the model registry from all config files, in-place.

    Useful for tests or after changing config files at runtime. Existing
    ``MODELS`` references see the new contents without re-importing.
    """
    fresh = _load_registry()
    MODELS.clear()
    MODELS.update(fresh)
    return MODELS


def get_llm_client(name: str, **overrides) -> CompletionClient:
    """Create a CompletionClient, optionally using registry config.

    If ``name`` is a registry key, its config (model_name, endpoint, API key,
    defaults) is applied. Otherwise ``name`` is passed directly to litellm,
    which handles routing for every common public provider.

    Args:
        name: Registry key or a litellm-supported model string.
        **overrides: Override any parameter (max_tokens, temperature, etc.)

    Returns:
        Configured CompletionClient.

    Example::

        # Pass-through to litellm (no registry entry needed)
        llm = get_llm_client("gpt-4o-mini")
        llm = get_llm_client("claude-sonnet-4-5-20250514")

        # Custom alias from llm_config.yaml
        llm = get_llm_client("my-internal-model", max_tokens=1000)
    """
    from unifiedllm import CompletionClient

    config = MODELS.get(name, {})

    if config:
        model = config.get("model_name", name)
        logger.info("LLM registry hit for %r → model=%r, api_base=%r", name, model, config.get("api_base"))
    else:
        model = name
        logger.debug("LLM registry miss for %r — passing through to litellm", name)

    params: dict[str, Any] = {
        "model": model,
        "drop_params": config.get("drop_params", True),
    }

    if api_base := config.get("api_base"):
        params["api_base"] = api_base

    if (api_key_env := config.get("api_key_env")) and (api_key := os.getenv(api_key_env)):
        params["api_key"] = api_key

    # Copy model-specific defaults from config (overrides win)
    for key in ("temperature", "top_p", "max_tokens"):
        if key in config and key not in overrides:
            params[key] = config[key]

    params.update(overrides)

    client = CompletionClient(**params)
    client._registry_config = config  # For context_window lookup
    return client
