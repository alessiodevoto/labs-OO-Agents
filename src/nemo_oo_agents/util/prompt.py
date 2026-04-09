# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Utility functions for prompt construction and manipulation.

These functions help manage prompt content, especially for limiting
token usage through truncation and sampling.
"""

from collections.abc import Iterable
from typing import Any


def preview(value: Any, max_tokens: int = 500) -> str:
    """
    Truncate value to approximately max_tokens.

    Uses simple heuristic: 4 characters ≈ 1 token

    Args:
        value: Value to truncate (str, list, dict, or any object)
        max_tokens: Maximum tokens to include (default: 500)

    Returns:
        Truncated string representation

    Examples:
        >>> preview("x" * 10000, 100)
        'xxxx...xxxx'

        >>> preview([1,2,3,4,5], 10)
        '[1, 2, 3, 4, 5]'

        >>> preview({"key": "value" * 1000}, 50)
        '{"key": "valuevalue...}'
    """
    max_chars = max_tokens * 4  # Heuristic: 4 chars per token

    # Convert to string
    if isinstance(value, str):
        text = value
    elif isinstance(value, list | dict):
        import json

        try:
            text = json.dumps(value, indent=None, default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value)

    # Truncate if needed
    if len(text) <= max_chars:
        return text

    # For long text, show beginning and end
    if len(text) > max_chars * 2:
        half = max_chars // 2
        return f"{text[:half]}...{text[-half:]}"
    else:
        return f"{text[:max_chars]}..."


def take(iterable: Iterable[Any], n: int) -> list[Any]:
    """
    Return first n items from iterable.

    Args:
        iterable: Any iterable (list, tuple, generator, etc.)
        n: Number of items to take

    Returns:
        List of first n items

    Examples:
        >>> take([1,2,3,4,5], 3)
        [1, 2, 3]

        >>> take(range(100), 5)
        [0, 1, 2, 3, 4]
    """
    if isinstance(iterable, list | tuple):
        return list(iterable[:n])

    # For generators and other iterables
    result = []
    for i, item in enumerate(iterable):
        if i >= n:
            break
        result.append(item)
    return result


def last(iterable: Iterable[Any], n: int) -> list[Any]:
    """
    Return last n items from iterable.

    Args:
        iterable: Any iterable (list, tuple, generator, etc.)
        n: Number of items to take

    Returns:
        List of last n items

    Examples:
        >>> last([1,2,3,4,5], 3)
        [3, 4, 5]

        >>> last(range(10), 5)
        [5, 6, 7, 8, 9]
    """
    if isinstance(iterable, list | tuple):
        return list(iterable[-n:])

    # For generators and other iterables, need to consume
    items = list(iterable)
    return items[-n:]
