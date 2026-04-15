# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared utilities for context-blocks."""

import re

from agentdoc import safe_pformat as safe_pformat  # noqa: F401 — re-exported for callers

# Hard character cap applied to pformat output **before** block-level truncation.
# This is a safety net that prevents OOM when a Python object (e.g. a 10 M-element
# list) is serialised for the LLM.  Block-level truncation (max_block_chars) still
# applies afterwards; this just stops the intermediate pformat from exhausting memory.
# 500 K is intentionally generous (25× the default max_block_chars of 20 K) so the
# block truncation always provides the LLM-facing limit.
_MAX_PRE_FORMAT_CHARS: int = 500_000


def camel_to_snake(name: str) -> str:
    """Convert CamelCase class name to snake_case (e.g. 'PythonOutput' -> 'python_output')."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def truncate_content(value: str, limit: int, format_type: str) -> tuple[str, bool]:
    """Truncate content if it exceeds the limit.

    Args:
        value: String content to potentially truncate.
        limit: Maximum characters allowed (must be > 0).
        format_type: Kept for API compatibility but ignored; the new format is
            format-agnostic.

    Returns:
        Tuple of (potentially_truncated_value, was_truncated).
    """
    if limit <= 0:
        raise ValueError(f"truncate_content limit must be > 0, got {limit}")
    if len(value) <= limit:
        return value, False

    total_chars = len(value)
    head_chars = limit // 2
    tail_chars = limit - head_chars
    head = value[:head_chars]
    tail = value[-tail_chars:]
    dropped = total_chars - head_chars - tail_chars

    result = (
        f"<truncated-output>\n"
        f"Output too large ({total_chars:,} chars). "
        f"Showing first {head_chars:,} and last {tail_chars:,} chars.\n\n"
        f"{head}\n\n"
        f"... {dropped:,} chars not shown ...\n\n"
        f"{tail}\n"
        f"</truncated-output>"
    )
    return result, True
