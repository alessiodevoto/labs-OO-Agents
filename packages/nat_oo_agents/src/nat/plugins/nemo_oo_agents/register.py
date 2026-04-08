# SPDX-License-Identifier: Apache-2.0
"""Plugin registration module for Agent006 NAT integration.

This module is the entry point discovered by NAT via:
    [project.entry-points.'nat.components']
    nat_nemo_oo_agents = "nat.plugins.nemo_oo_agents.register"

Importing this module triggers decorator-based registration of:
- nemo_oo_agents_wrapper workflow type
- nemo_oo_agents LLM client wrappers (OpenAI, NIM, LiteLLM)
"""

# Import modules to trigger @register_function / @register_llm_client decorators
from . import (
    llm,  # noqa: F401
    nemo_oo_agents_wrapper,  # noqa: F401
)
