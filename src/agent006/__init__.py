"""
agent006 - Code-Generating Agent Orchestration System

A minimal viable runtime for agent orchestration with event sourcing,
serialized execution, and complete transparency.
"""

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Library logging: add NullHandler so applications that don't configure
# logging never see "No handlers could be found for logger 'agent006'".
# This is the only handler a library should ever add (see Python docs).
# ---------------------------------------------------------------------------
import logging as _logging

_logging.getLogger(__name__).addHandler(_logging.NullHandler())

# Export core types
# Export agent and decorators

from agent006._logging import enable_logging  # noqa: E402
from agent006._visible import visible  # noqa: E402
from agent006.agent import Agent  # noqa: E402
from agent006.decorators import strategy  # noqa: E402

# Export errors
from agent006.errors import (  # noqa: E402
    Agent006Error,
    Agent006RuntimeError,
    GenerationError,
    RestrictedCodeError,
    SerializationError,
    SnapshotNotFoundError,
    StorageNotConfiguredError,
    ValidationError,
)
from agent006.library_manager import LibraryManager  # noqa: E402
from agent006.library_skill import LibrarySkill  # noqa: E402
from agent006.media import Audio, File, Image, Media  # noqa: E402
from agent006.metaclass import AgentMeta, no_trace  # noqa: E402

# Export prompt inspection utilities
from agent006.prompts import PromptData, build_prompt_data, print_prompt  # noqa: E402

# Export runtime API classes
from agent006.runtime.context import ContextApi  # noqa: E402
from agent006.runtime.context_manager import ContextManager  # noqa: E402

# Export event filtering
from agent006.runtime.event_query import EventQuery  # noqa: E402
from agent006.runtime.events import EventsApi  # noqa: E402
from agent006.skill import Skill, TextSkill  # noqa: E402
from agent006.skill_manager import SkillManager  # noqa: E402

# Export storage
from agent006.storage import StorageManager  # noqa: E402

# Export strategy base class and implementations
from agent006.strategies import (  # noqa: E402
    CodeActLiteStrategy,
    CodeActStrategy,
    GenerationStrategy,
    InspectInputsPrefill,
    PredictStrategy,
    PurePythonStrategy,
    ReflexionStrategy,
    get_default_strategy,
    set_default_strategy,
)
from agentdoc import hidden  # noqa: E402
from context_blocks import DynamicContext  # noqa: E402
from unifiedllm import LLMResponse  # noqa: E402

__all__ = [
    "__version__",
    # Types
    "DynamicContext",  # Re-exported from context_blocks
    "EventQuery",  # Event filtering configuration
    "ContextApi",  # LLM-facing context API wrapper (Skill)
    "ContextManager",  # Context block state backend
    "EventsApi",  # Runtime events query API
    "LLMResponse",  # Re-exported from unifiedllm
    # Strategies
    "GenerationStrategy",
    "PurePythonStrategy",
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
    "SkillManager",
    "LibrarySkill",
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
    "Agent006Error",
    "GenerationError",
    "ValidationError",
    "RestrictedCodeError",
    "Agent006RuntimeError",
    "SerializationError",
    "SnapshotNotFoundError",
    "StorageNotConfiguredError",
    # Storage
    "StorageManager",
]

# Install debug handler by default (zero overhead until SIGUSR2 received)
# Usage: kill -USR2 <pid> → dumps traceback + cell code to ~/.cache/agent006/
from agent006.runtime.debug_handler import install_debug_handler  # noqa: E402

install_debug_handler()

# Register agentdoc provider for Agent class
# This is done here (at the end of agent006 import) rather than in agentdoc's
# auto-registration because of circular import issues: agentdoc is imported
# during agent006 import (via actor.py), so agentdoc's _try_register_agent006()
# fails silently during the circular import.
try:
    from agentdoc.providers.agent006 import Agent006Provider  # type: ignore[import-untyped]

    _register_provider = getattr(__import__("agentdoc"), "register_provider", None)
    if _register_provider is not None:
        _register_provider(Agent, Agent006Provider())
except (ImportError, AttributeError):
    pass  # agentdoc not installed or provider not available
