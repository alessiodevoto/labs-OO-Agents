# SPDX-License-Identifier: Apache-2.0
"""LLM bridge: register NeMo OO Agents as a framework type in NAT's LLM client registry.

Each NAT LLM provider gets an 'nooa' wrapper that constructs a
CompletionClient. The NAT YAML config is the primary source for api_key,
base_url, model, etc. If values are missing, we fall back to the
unifiedllm model registry for defaults (endpoint, api key env, routing).
"""

import logging

from nat.builder.builder import Builder
from nat.cli.register_workflow import register_llm_client

from .nooa_wrapper import NEMO_OO_AGENTS_FRAMEWORK

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
    from nooa.unifiedllm import (
        CompletionClient,
        resolve_api_key_from_config,
    )

    # Check the registry for defaults via the public snapshot accessor.
    # get_registry_config() triggers the lazy auto-load (so an
    # un-bootstrapped nat process still sees registered aliases instead of
    # an empty registry) and returns a defensive copy taken under the
    # registry lock — no coupling to the module-private MODELS /
    # _registry_lock.
    #
    # Catch broadly: a broken llm_config.yaml or any other
    # registry-load failure must not abort NAT client construction —
    # NAT config alone is sufficient to build the client.
    try:
        from nooa.unifiedllm import get_registry_config

        registry_config = get_registry_config(model_name)
    except Exception as exc:
        logger.debug(
            "Registry defaults unavailable; proceeding with NAT config only: %s",
            exc,
        )
        registry_config = {}

    # Resolve api_base: NAT config wins, then registry, then nothing
    resolved_base = base_url or registry_config.get("api_base")

    # Resolve API key: NAT config wins, then registry env var (helper
    # logs a WARN if api_key_env is set in registry but the env var is
    # unset).
    resolved_key = api_key
    if not resolved_key:
        resolved_key = resolve_api_key_from_config(model_name, registry_config)

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

    # Use model_name from registry config if available (includes litellm routing prefix),
    # otherwise pass the name directly and let litellm handle routing.
    litellm_model = registry_config.get("model_name", model_name)

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
    async def openai_nooa(config: OpenAIModelConfig, _builder: Builder):
        yield _build_llm(
            model_name=config.model_name,
            api_key=_get_secret_value(config.api_key),
            base_url=config.base_url,
            temperature=config.temperature,
        )

except ImportError:
    logger.debug("OpenAI LLM config not available, skipping nooa registration")


# ---------------------------------------------------------------------------
# NIM provider
# ---------------------------------------------------------------------------

try:
    from nat.llm.nim_llm import NIMModelConfig

    @register_llm_client(config_type=NIMModelConfig, wrapper_type=NEMO_OO_AGENTS_FRAMEWORK)
    async def nim_nooa(config: NIMModelConfig, _builder: Builder):
        yield _build_llm(
            model_name=config.model_name,
            api_key=_get_secret_value(config.api_key),
            base_url=config.base_url,
            temperature=config.temperature,
        )

except ImportError:
    logger.debug("NIM LLM config not available, skipping nooa registration")


# ---------------------------------------------------------------------------
# LiteLLM provider (generic fallback)
# ---------------------------------------------------------------------------

try:
    from nat.llm.litellm_llm import LiteLLMModelConfig

    @register_llm_client(config_type=LiteLLMModelConfig, wrapper_type=NEMO_OO_AGENTS_FRAMEWORK)
    async def litellm_nooa(config: LiteLLMModelConfig, _builder: Builder):
        yield _build_llm(
            model_name=config.model_name,
            api_key=_get_secret_value(config.api_key),
            base_url=config.base_url,
            temperature=config.temperature,
        )

except ImportError:
    logger.debug("LiteLLM config not available, skipping nooa registration")
