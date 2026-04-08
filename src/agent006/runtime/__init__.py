"""Runtime components: actor loop, prompts, PLAN, REPL, context.

Core runtime contains NO OpenTelemetry code - only the hooks protocol.
For tracing, use: from openinference_instrumentation_agent006 import enable_tracing

Middleware types are imported directly from ``agent006.runtime.middleware``::

    from agent006.runtime.middleware import LLMCallContext, LLMCallMiddleware
"""

from agent006.config.truncation_config import TruncationConfig
from agent006.runtime.actor import ActorRuntime
from agent006.runtime.event_manager import EventManager
from agent006.runtime.event_query import EventQuery
from agent006.runtime.events import EventsApi
from agent006.runtime.hooks import InstrumentationHooks, get_hooks, set_hooks
from agent006.runtime.media_capture import show
from agent006.runtime.pprint import pprint
from agent006.runtime.truncating_stream import TruncatingStringIO

__all__ = [
    "ActorRuntime",
    # Event system
    "EventManager",
    "EventQuery",
    "EventsApi",
    # Hook-based instrumentation protocol
    "InstrumentationHooks",
    "set_hooks",
    "get_hooks",
    # Truncation system
    "TruncationConfig",
    "TruncatingStringIO",
    "pprint",
    "show",
]
