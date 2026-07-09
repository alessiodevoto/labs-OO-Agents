# SPDX-License-Identifier: Apache-2.0
"""Plugin registration module for NeMo OO Agents NAT integration.

This module is the entry point discovered by NAT via:
    [project.entry-points.'nat.components']
    nat_nooa = "nat.plugins.nooa.register"

Importing this module triggers decorator-based registration of:
- nooa_wrapper workflow type
- nooa LLM client wrappers (OpenAI, NIM, LiteLLM)
"""

# Import modules to trigger @register_function / @register_llm_client decorators
from . import (
    llm,  # noqa: F401
    nooa_wrapper,  # noqa: F401
)
