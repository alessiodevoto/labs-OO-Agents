# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Runtime components: actor loop, prompts, PLAN, REPL, context.

Core runtime contains NO OpenTelemetry code - only the hooks protocol.
For tracing, use: from openinference_instrumentation_nemo_oo_agents import enable_tracing

Middleware types are imported directly from ``nemo_oo_agents.runtime.middleware``::

    from nemo_oo_agents.runtime.middleware import LLMCallContext, LLMCallMiddleware
"""

from nemo_oo_agents.agentdoc import TruncatingStringIO
from nemo_oo_agents.config.truncation_config import TruncationConfig
from nemo_oo_agents.runtime.actor import ActorRuntime
from nemo_oo_agents.runtime.event_manager import EventManager
from nemo_oo_agents.runtime.event_query import EventQuery
from nemo_oo_agents.runtime.events import EventsApi
from nemo_oo_agents.runtime.hooks import InstrumentationHooks, get_hooks, set_hooks
from nemo_oo_agents.runtime.media_capture import show
from nemo_oo_agents.runtime.pprint import pprint

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
