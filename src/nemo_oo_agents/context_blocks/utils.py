# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared utilities for context-blocks."""

import re

from nemo_oo_agents.agentdoc import truncating_pformat as truncating_pformat


def camel_to_snake(name: str) -> str:
    """Convert CamelCase class name to snake_case (e.g. 'PythonOutput' -> 'python_output')."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
