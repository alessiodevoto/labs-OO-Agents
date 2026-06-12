# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
nemo_oo_agents - Code-Generating Agent Orchestration System

A minimal viable runtime for agent orchestration with event sourcing,
serialized execution, and complete transparency.
"""

import logging as _logging

from nemo_oo_agents._version import __version__

# ---------------------------------------------------------------------------
# Library logging: add NullHandler so applications that don't configure
# logging never see "No handlers could be found for logger 'nemo_oo_agents'".
# This is the only handler a library should ever add (see Python docs).
# ---------------------------------------------------------------------------
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

# Export core types
# Export agent and decorators

from nemo_oo_agents._logging import enable_logging  # noqa: E402
from nemo_oo_agents._visible import visible  # noqa: E402
from nemo_oo_agents.agent import Agent  # noqa: E402
from nemo_oo_agents.agentdoc import hidden  # noqa: E402
from nemo_oo_agents.context_blocks import ContextWindowStats, DynamicContext  # noqa: E402
from nemo_oo_agents.decorators import strategy  # noqa: E402

# Export errors
from nemo_oo_agents.errors import (  # noqa: E402
    DynamicMethodAdditionError,
    GenerationError,
    NemoOOAgentsError,
    NemoOOAgentsRuntimeError,
    RestrictedCodeError,
    SerializationError,
    SnapshotNotFoundError,
    StorageNotConfiguredError,
    ValidationError,
)
from nemo_oo_agents.library_manager import LibraryManager  # noqa: E402
from nemo_oo_agents.media import Audio, File, Image, Media  # noqa: E402
from nemo_oo_agents.metaclass import AgentMeta, no_trace  # noqa: E402

# Export prompt inspection utilities
from nemo_oo_agents.prompts import PromptData, build_prompt_data, print_prompt  # noqa: E402
from nemo_oo_agents.runtime.channels import Channel, QueueManager, QueueOutput  # noqa: E402

# Export runtime API classes
from nemo_oo_agents.runtime.context import ContextApi  # noqa: E402
from nemo_oo_agents.runtime.context_manager import ContextManager  # noqa: E402

# Export event filtering
from nemo_oo_agents.runtime.event_query import EventQuery  # noqa: E402
from nemo_oo_agents.runtime.events import EventsApi  # noqa: E402
from nemo_oo_agents.skill import Skill, TextSkill, get_slash_commands, slash_command  # noqa: E402
from nemo_oo_agents.skill_registry import skill_from_module  # noqa: E402

# Export storage
from nemo_oo_agents.storage import StorageManager  # noqa: E402

# Export strategy base class and implementations
from nemo_oo_agents.strategies import (  # noqa: E402
    CodeActLiteStrategy,
    CodeActStrategy,
    GenerationStrategy,
    InspectInputsPrefill,
    PredictStrategy,
    ReflexionStrategy,
    get_default_strategy,
    set_default_strategy,
)
from nemo_oo_agents.token_counter import char_approximate_token_counter  # noqa: E402
from nemo_oo_agents.unifiedllm import LLMResponse  # noqa: E402


# Lazy re-export of llm_config_chain — defer importing the llm_config /
# paths machinery (and the registry it touches) until the helper is
# actually called, keeping ``import nemo_oo_agents`` cheap.
def __getattr__(name):
    if name == "llm_config_chain":
        from nemo_oo_agents.llm_config import llm_config_chain

        return llm_config_chain
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    "llm_config_chain",  # Lazy re-export (see __getattr__)
    # Types
    "ContextWindowStats",  # Re-exported from context_blocks
    "DynamicContext",  # Re-exported from context_blocks
    "EventQuery",  # Event filtering configuration
    "ContextApi",  # LLM-facing context API wrapper (Skill)
    "ContextManager",  # Context block state backend
    "EventsApi",  # Runtime events query API
    "Channel",  # Named queue/event channel for agent input
    "QueueManager",  # Channel registry + race + status
    "QueueOutput",  # Event emitted by event-mode channels
    "LLMResponse",  # Re-exported from unifiedllm
    # Strategies
    "GenerationStrategy",
    "CodeActStrategy",
    "CodeActLiteStrategy",
    "ReflexionStrategy",
    "PredictStrategy",
    "get_default_strategy",
    "set_default_strategy",
    # Prefill plugins
    "InspectInputsPrefill",
    # Prompt inspection
    "print_prompt",
    "build_prompt_data",
    "PromptData",
    # Media types
    "Media",
    "Image",
    "Audio",
    "File",
    # Agent and decorators
    "Agent",
    "Skill",
    "TextSkill",
    "slash_command",
    "get_slash_commands",
    "skill_from_module",
    "LibraryManager",
    "strategy",
    "no_trace",
    "AgentMeta",
    # Logging
    "enable_logging",
    # Visibility
    "hidden",
    "visible",
    # Errors
    "NemoOOAgentsError",
    "GenerationError",
    "ValidationError",
    "RestrictedCodeError",
    "NemoOOAgentsRuntimeError",
    "DynamicMethodAdditionError",
    "SerializationError",
    "SnapshotNotFoundError",
    "StorageNotConfiguredError",
    # Storage
    "StorageManager",
    # Token counting
    "char_approximate_token_counter",
]

# Install debug handler by default (zero overhead until SIGUSR2 received)
# Usage: kill -USR2 <pid> → dumps traceback + cell code to debug_dump_<pid>.txt in cwd
from nemo_oo_agents.runtime.debug_handler import install_debug_handler  # noqa: E402

install_debug_handler()
