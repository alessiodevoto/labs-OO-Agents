# SPDX-License-Identifier: Apache-2.0
"""Plugin registration module for Agent006 NAT integration.

This module is the entry point discovered by NAT via:
    [project.entry-points.'nat.components']
    nat_agent006 = "nat.plugins.agent006.register"

Importing this module triggers decorator-based registration of:
- agent006_wrapper workflow type
- agent006 LLM client wrappers (OpenAI, NIM, LiteLLM)
"""

# Import modules to trigger @register_function / @register_llm_client decorators
from . import (
    agent006_wrapper,  # noqa: F401
    llm,  # noqa: F401
)
