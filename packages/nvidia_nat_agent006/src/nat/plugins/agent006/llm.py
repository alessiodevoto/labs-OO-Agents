# SPDX-License-Identifier: Apache-2.0
"""LLM bridge: register Agent006 as a framework type in NAT's LLM client registry.

Each NAT LLM provider gets an 'nemo_oo_agents' wrapper that constructs a
CompletionClient. The NAT YAML config is the primary source for api_key,
base_url, model, etc. If values are missing, we fall back to the
unifiedllm model registry for defaults (endpoint, api key env, routing).
"""

import logging

from nat.builder.builder import Builder
from nat.cli.register_workflow import register_llm_client

from .nemo_oo_agents_wrapper import NEMO_OO_AGENTS_FRAMEWORK

logger = logging.getLogger(__name__)


def _get_secret_value(secret) -> str | None:
    """Extract string value from a SecretStr or plain string."""
    if secret is None:
        return None
    if hasattr(secret, "get_secret_value"):
        return secret.get_secret_value()
    return str(secret)


def _build_llm(
    model_name: str, api_key: str | None, base_url: str | None, temperature: float | None
):
    """Build a CompletionClient from NAT config, with registry fallback.

    Priority: NAT config > registry defaults > nothing.
    """
    import os

    from unifiedllm import CompletionClient

    # Check the registry for defaults
    try:
        from unifiedllm.registry import MODELS

        registry_config = MODELS.get(model_name, {})
    except ImportError:
        registry_config = {}

    # Resolve endpoint: NAT config wins, then registry, then nothing
    resolved_base = base_url or registry_config.get("endpoint")

    # Resolve API key: NAT config wins, then registry env var
    resolved_key = api_key
    if not resolved_key:
        key_env = registry_config.get("api_key_env")
        if key_env:
            resolved_key = os.getenv(key_env)

    # Build params
    params: dict = {"drop_params": True}
    if resolved_key:
        params["api_key"] = resolved_key
    if resolved_base:
        params["api_base"] = resolved_base
    if temperature is not None:
        params["temperature"] = temperature
    elif "temperature" in registry_config:
        params["temperature"] = registry_config["temperature"]

    # Copy other registry defaults (top_p, max_tokens)
    for key in ("top_p", "max_tokens"):
        if key in registry_config:
            params[key] = registry_config[key]

    # When routing through an OpenAI-compatible gateway, prefix with
    # "openai/" so litellm uses its OpenAI handler.
    litellm_model = model_name
    if resolved_base and not model_name.startswith("openai/"):
        litellm_model = f"openai/{model_name}"

    llm = CompletionClient(model=litellm_model, **params)
    logger.info(
        "Created CompletionClient for model: %s (base_url: %s)",
        litellm_model,
        resolved_base or "default",
    )
    return llm


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

try:
    from nat.llm.openai_llm import OpenAIModelConfig

    @register_llm_client(config_type=OpenAIModelConfig, wrapper_type=NEMO_OO_AGENTS_FRAMEWORK)
    async def openai_nemo_oo_agents(config: OpenAIModelConfig, _builder: Builder):
        yield _build_llm(
            model_name=config.model_name,
            api_key=_get_secret_value(config.api_key),
            base_url=config.base_url,
            temperature=config.temperature,
        )

except ImportError:
    logger.debug("OpenAI LLM config not available, skipping nemo_oo_agents registration")


# ---------------------------------------------------------------------------
# NIM provider
# ---------------------------------------------------------------------------

try:
    from nat.llm.nim_llm import NIMModelConfig

    @register_llm_client(config_type=NIMModelConfig, wrapper_type=NEMO_OO_AGENTS_FRAMEWORK)
    async def nim_nemo_oo_agents(config: NIMModelConfig, _builder: Builder):
        yield _build_llm(
            model_name=config.model_name,
            api_key=_get_secret_value(config.api_key),
            base_url=config.base_url,
            temperature=config.temperature,
        )

except ImportError:
    logger.debug("NIM LLM config not available, skipping nemo_oo_agents registration")


# ---------------------------------------------------------------------------
# LiteLLM provider (generic fallback)
# ---------------------------------------------------------------------------

try:
    from nat.llm.litellm_llm import LiteLLMModelConfig

    @register_llm_client(config_type=LiteLLMModelConfig, wrapper_type=NEMO_OO_AGENTS_FRAMEWORK)
    async def litellm_nemo_oo_agents(config: LiteLLMModelConfig, _builder: Builder):
        yield _build_llm(
            model_name=config.model_name,
            api_key=_get_secret_value(config.api_key),
            base_url=config.base_url,
            temperature=config.temperature,
        )

except ImportError:
    logger.debug("LiteLLM config not available, skipping nemo_oo_agents registration")
