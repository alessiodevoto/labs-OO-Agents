"""Agent utilities for the evaluation pipeline.

This module provides:
- AgentWrapper: Adapts agents to the pipeline's run() interface
- load_agent_class: Loads agent class from module/class names
- create_agent_factory: Creates factory functions for agent instantiation
"""

import importlib
import os
from typing import Any

from .config import TestConfig


class AgentWrapper:
    """Wraps any agent to conform to pipeline's run() interface."""

    def __init__(self, agent_instance, method_name: str):
        self.agent = agent_instance
        self.method_name = method_name
        self.method = getattr(agent_instance, method_name)

    async def run(self, input: tuple) -> Any:
        """Run agent method with args/kwargs unpacking."""
        args, kwargs = input
        return await self.method(*args, **kwargs)


def load_agent_class(test: TestConfig) -> type:
    """Load agent class from test config."""
    module = importlib.import_module(test.agent_module)
    return getattr(module, test.agent_class)


def client_from_spec(spec):
    """Create LLM client from ModelSpec."""
    from unifiedllm import CompletionClient, RetryConfig

    # Build config dict
    config = {
        "model": spec.model_name,
        "api_base": spec.endpoint,
        "api_key": os.getenv(spec.api_key_env or "", ""),
        "max_tokens": spec.max_tokens or 4096,
    }

    # Add reasoning support for Claude and other models
    if spec.reasoning_effort:
        config["reasoning_effort"] = spec.reasoning_effort
        # Tell litellm to allow reasoning_effort through for OpenAI-compatible endpoints
        config["allowed_openai_params"] = ["reasoning_effort"]

    # Add nvext for Nemotron-style thinking tokens
    if spec.max_thinking_tokens:
        config["extra_body"] = {"nvext": {"max_thinking_tokens": spec.max_thinking_tokens}}

    # Build retry config if specified
    retry_config = None
    if spec.max_retries is not None or spec.retry_on_empty_content:
        retry_config = RetryConfig(
            max_retries=spec.max_retries if spec.max_retries is not None else 3,
            retry_on_empty_content=spec.retry_on_empty_content,
        )

    return CompletionClient(retry_config=retry_config, **config)


def agent_from_spec(spec) -> AgentWrapper:
    """Reconstruct an agent from an AgentSpec in a subprocess.

    Imports the agent class by module path (or file path for --agent flag),
    creates a fresh LLM client from the config dict, and wraps the result.
    """
    import importlib.util

    if spec.agent_file:
        # File-based agent (--agent flag): load from file path
        import sys
        from pathlib import Path

        path = Path(spec.agent_file)
        module_name = f"_agent_subprocess_{path.stem}"
        file_spec = importlib.util.spec_from_file_location(module_name, path)
        if file_spec is None or file_spec.loader is None:
            raise ImportError(f"Cannot load agent from file: {spec.agent_file}")
        mod = importlib.util.module_from_spec(file_spec)
        sys.modules[module_name] = mod
        file_spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(spec.agent_module)
    cls = getattr(mod, spec.agent_class)

    # Build client from config dict (empty config → no LLM needed)
    client = None
    if spec.client_config and spec.client_config.get("model"):
        from unifiedllm import CompletionClient, RetryConfig

        cc = dict(spec.client_config)
        # Resolve API key from env var name at construction time
        api_key_env = cc.pop("api_key_env", None)
        if api_key_env:
            cc.setdefault("api_key", os.getenv(api_key_env, ""))

        # Extract retry config if present
        retry_kwargs = cc.pop("retry_config", None)
        retry_config = RetryConfig(**retry_kwargs) if retry_kwargs else None

        client = CompletionClient(retry_config=retry_config, **cc)

    agent_instance = cls(llm=client)
    return AgentWrapper(agent_instance, spec.method)


def create_agent_factory(agent_class: type, method_name: str, model=None):
    """Create agent factory from agent class.

    Returns a callable that creates wrapped agent instances.
    """
    from .model_factory import client as model_client

    def factory():
        if model is None:
            agent_instance = agent_class()
        elif hasattr(model, "model_name"):
            agent_instance = agent_class(llm=client_from_spec(model))
        else:
            agent_instance = agent_class(llm=model_client(model))
        return AgentWrapper(agent_instance, method_name)

    return factory
