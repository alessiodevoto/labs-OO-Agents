# SPDX-License-Identifier: Apache-2.0
"""NeMo OO Agents wrapper for NVIDIA NeMo Agent Toolkit.

Wraps NeMo OO Agents agents as NAT workflow functions, enabling them to be
configured, run, and observed through NAT's infrastructure.
"""

import importlib.util
import inspect
import logging
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from types import NoneType
from typing import Any

from dotenv import load_dotenv
from nat.builder.builder import Builder
from nat.builder.function import Function
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, ConfigDict, DirectoryPath, Field, FilePath

logger = logging.getLogger(__name__)

# Framework identifier for LLM client registry
NEMO_OO_AGENTS_FRAMEWORK = "nooa"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class NemoOOAgentsWrapperInput(BaseModel):
    """Input model for the NeMo OO Agents wrapper."""

    model_config = ConfigDict(extra="allow")
    messages: list[Any] | str


class NemoOOAgentsWrapperOutput(BaseModel):
    """Output model for the NeMo OO Agents wrapper."""

    model_config = ConfigDict(extra="allow")
    content: str


class NemoOOAgentsWrapperConfig(FunctionBaseConfig, name="nooa_wrapper"):
    """Configuration model for the NeMo OO Agents wrapper.

    Example YAML::

        workflow:
          _type: nooa_wrapper
          agent: path/to/module.py:ClassName
          method: chat
          dependencies:
            - ./src
          tools:
            - current_time
          env: .env
    """

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    dependencies: list[DirectoryPath] = Field(default_factory=list)
    agent: str = Field(
        ...,
        description="Path to agent module and class: 'path/to/module.py:ClassName'",
    )
    method: str = Field(
        ...,
        description="Name of the async method to invoke on the agent",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="List of NAT tool names to inject onto the agent",
    )
    llm_name: str | None = Field(
        default=None,
        description="Name of NAT-configured LLM (from llms: section). "
        "If omitted, agent uses its own LLM.",
    )
    env: FilePath | dict[str, str] | None = Field(
        default=None,
        description="Path to .env file or dict of environment variables",
    )
    enable_tracing: bool = Field(
        default=True,
        description="Whether to enable NeMo OO Agents OTel tracing",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP HTTP endpoint for exporting NeMo OO Agents spans "
        "(e.g. 'http://localhost:4318/v1/traces'). When set (and enable_tracing "
        "is True), spans are exported to this collector alongside the JSONL "
        "trace viewer. Opt-in: no OTLP export is configured when omitted.",
    )


# ---------------------------------------------------------------------------
# NAT Function implementation
# ---------------------------------------------------------------------------


class NemoOOAgentsWrapperFunction(
    Function[NemoOOAgentsWrapperInput, NoneType, NemoOOAgentsWrapperOutput]
):
    """NAT Function that wraps an NeMo OO Agents agent method."""

    def __init__(
        self,
        *,
        config: NemoOOAgentsWrapperConfig,
        description: str | None = None,
        agent: Any,
        method_name: str,
    ):
        super().__init__(
            config=config,
            description=description,
            converters=[NemoOOAgentsWrapperFunction.convert_to_str],
        )
        self._agent = agent
        self._method_name = method_name
        self._method = getattr(agent, method_name)

    def _convert_input(self, value: Any) -> NemoOOAgentsWrapperInput:
        """Convert raw input to NemoOOAgentsWrapperInput."""
        if isinstance(value, str):
            return NemoOOAgentsWrapperInput(messages=value)
        if isinstance(value, dict):
            # Handle dict with 'messages' key or single message
            if "messages" in value:
                return NemoOOAgentsWrapperInput(**value)
            if "content" in value:
                return NemoOOAgentsWrapperInput(messages=value["content"])
            if "input" in value:
                return NemoOOAgentsWrapperInput(messages=value["input"])
        if isinstance(value, list):
            # List of messages -- extract last message text
            if value and hasattr(value[-1], "content"):
                return NemoOOAgentsWrapperInput(messages=value[-1].content)
            if value and isinstance(value[-1], str):
                return NemoOOAgentsWrapperInput(messages=value[-1])
            if value and isinstance(value[-1], dict):
                return NemoOOAgentsWrapperInput(messages=value[-1].get("content", str(value[-1])))
        return NemoOOAgentsWrapperInput(messages=str(value))

    async def _ainvoke(self, value: NemoOOAgentsWrapperInput) -> NemoOOAgentsWrapperOutput:
        """Invoke the NeMo OO Agents method with the input message."""
        # Extract the message text
        if isinstance(value.messages, list):
            # Take the last message
            last = value.messages[-1] if value.messages else ""
            if hasattr(last, "content"):
                message_text = last.content
            elif isinstance(last, dict):
                message_text = last.get("content", str(last))
            else:
                message_text = str(last)
        else:
            message_text = str(value.messages)

        try:
            result = await self._method(message_text)
            return NemoOOAgentsWrapperOutput(content=str(result))
        except Exception as e:
            raise RuntimeError(
                f"Error in NeMo OO Agents agent method '{self._method_name}': {e}"
            ) from e

    async def _astream(
        self, value: NemoOOAgentsWrapperInput
    ) -> AsyncGenerator[NemoOOAgentsWrapperOutput, None]:
        """Streaming not yet supported -- falls back to single invoke."""
        result = await self._ainvoke(value)
        yield result

    @staticmethod
    def convert_to_str(value: NemoOOAgentsWrapperOutput) -> str:
        """Convert output to string."""
        return value.content


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@register_function(config_type=NemoOOAgentsWrapperConfig)
async def register(config: NemoOOAgentsWrapperConfig, b: Builder):
    """Register an NeMo OO Agents agent as a NAT workflow function.

    This function:
    1. Adds dependencies to sys.path
    2. Loads environment variables
    3. Sets up OTel bridge (if configured)
    4. Dynamically imports the agent module and class
    5. Resolves LLM (NAT-configured or agent's own)
    6. Instantiates the agent
    7. Injects NAT tools as native Python classes
    8. Validates the target method
    9. Yields the wrapper function
    """
    added_paths: list[str] = []

    try:
        # 1. Process dependencies -- add to sys.path
        for dependency in config.dependencies:
            dep_str = str(dependency)
            if os.path.exists(dep_str) and os.path.isdir(dep_str):
                sys.path.insert(0, dep_str)
                added_paths.append(dep_str)
            else:
                raise ValueError(f"Dependency '{dep_str}' is not a valid directory.")

        # 2. Process environment variables
        if config.env is not None:
            if isinstance(config.env, Path):
                if config.env.exists() and config.env.is_file():
                    load_dotenv(config.env, override=True)
                else:
                    raise ValueError(f"Env file '{config.env}' not found.")
            elif isinstance(config.env, dict):
                for key, value in config.env.items():
                    os.environ[key] = value

        # 3. Enable NeMo OO Agents JSONL tracing first (creates TracerProvider with resource),
        #    then set up OTel bridge (piggybacks on existing provider for OTLP export).
        if config.enable_tracing:
            # Enable NeMo OO Agents's own tracing first -- this creates the TracerProvider
            # with full resource metadata (including tags) attached.
            try:
                from nooa.tracing import enable_tracing

                enable_tracing(
                    extra_resource_attrs={"tags": ["nat_integration"]},
                )
                logger.info("NeMo OO Agents tracing enabled")
            except ImportError:
                logger.debug("NeMo OO Agents tracing instrumentation not available")

            # Now set up the OTel bridge -- it will detect the existing provider
            # and add any OTLP exporters alongside the JSONL exporter.
            from .otel_bridge import setup_shared_tracer

            setup_shared_tracer(config.otlp_endpoint)

        # 4. Dynamically import the agent module + class
        if config.agent.count(":") != 1:
            raise ValueError(
                f"Agent definition '{config.agent}' must be in format "
                f"'path/to/module.py:ClassName'. "
                f"Found {config.agent.count(':')} colons."
            )

        module_path, class_name = config.agent.rsplit(":", 1)
        unique_module_name = f"nooa_wrapper_{uuid.uuid4().hex[:8]}"

        spec = importlib.util.spec_from_file_location(unique_module_name, module_path)
        if spec is None:
            raise ValueError(f"Could not find module: {module_path}")

        module = importlib.util.module_from_spec(spec)
        if module is None:
            raise ValueError(f"Could not create module: {module_path}")

        sys.modules[unique_module_name] = module

        if spec.loader is not None:
            spec.loader.exec_module(module)
        else:
            raise ValueError(f"No loader for module: {module_path}")

        AgentClass = getattr(module, class_name, None)
        if AgentClass is None:
            raise ValueError(f"Class '{class_name}' not found in module '{module_path}'")

        # 5. Resolve LLM
        llm = None
        if config.llm_name:
            try:
                llm = await b.get_llm(config.llm_name, wrapper_type=NEMO_OO_AGENTS_FRAMEWORK)
                logger.info(
                    "Using NAT-configured LLM '%s' for NeMo OO Agents agent",
                    config.llm_name,
                )
            except Exception as e:
                logger.warning(
                    "Could not get NAT LLM '%s': %s. Agent will use its own LLM.",
                    config.llm_name,
                    e,
                )

        # 6. Instantiate the agent
        if llm is not None:
            agent = AgentClass(llm=llm)
        else:
            agent = AgentClass()

        # 7. Inject NAT tools as native Python classes
        if config.tools:
            from .tool_bridge import inject_nat_tools

            await inject_nat_tools(agent, config.tools, b)

        # 8. Validate the target method
        method = getattr(agent, config.method, None)
        if method is None:
            available = [
                m for m in dir(agent) if not m.startswith("_") and callable(getattr(agent, m))
            ]
            raise ValueError(
                f"Method '{config.method}' not found on {class_name}. "
                f"Available methods: {available}"
            )
        if not inspect.iscoroutinefunction(method):
            raise ValueError(f"Method '{config.method}' on {class_name} must be async.")

        logger.info(
            "NeMo OO Agents wrapper ready: %s.%s",
            class_name,
            config.method,
        )

        # 9. Yield the wrapper function
        yield NemoOOAgentsWrapperFunction(
            config=config,
            description=config.description or f"{class_name}.{config.method}",
            agent=agent,
            method_name=config.method,
        )

    finally:
        # Clean up sys.path
        for dep in added_paths:
            if dep in sys.path:
                sys.path.remove(dep)
