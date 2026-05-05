# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared utilities for context-blocks."""

import re


# Lazy import to avoid circular dependency:
# context_blocks -> nemo_oo_agents.agentdoc -> nemo_oo_agents -> ... -> context_blocks
def truncating_pformat(*args, **kwargs):  # noqa: F811
    from nemo_oo_agents.agentdoc import truncating_pformat as _real

    return _real(*args, **kwargs)


def camel_to_snake(name: str) -> str:
    """Convert CamelCase class name to snake_case (e.g. 'PythonOutput' -> 'python_output')."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
