# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Logger utility for structured logging.

Usage in generated code (utilities are pre-imported):
    logger.info("Processing started")
    logger.debug("Item count", count=len(items))
    logger.warning("Rate limit approaching", current=90, limit=100)
"""

import logging
from typing import Any

from nemo_oo_agents.util._context import _current_agent


def debug(message: str, **kwargs: Any) -> None:
    """
    Log a debug message.

    Args:
        message: Log message
        **kwargs: Additional structured data

    Example:
        logger.debug("Variable value", var=x, type=type(x).__name__)
    """
    _log("DEBUG", message, kwargs)


def info(message: str, **kwargs: Any) -> None:
    """
    Log an info message.

    Args:
        message: Log message
        **kwargs: Additional structured data

    Example:
        logger.info("Processing started", item_count=len(items))
    """
    _log("INFO", message, kwargs)


def warning(message: str, **kwargs: Any) -> None:
    """
    Log a warning message.

    Args:
        message: Log message
        **kwargs: Additional structured data

    Example:
        logger.warning("Rate limit approaching", current=90, limit=100)
    """
    _log("WARNING", message, kwargs)


def error(message: str, **kwargs: Any) -> None:
    """
    Log an error message.

    Args:
        message: Log message
        **kwargs: Additional structured data

    Example:
        logger.error("Processing failed", error_type=type(e).__name__)
    """
    _log("ERROR", message, kwargs)


def _log(level: str, message: str, data: dict[str, Any]) -> None:
    """Internal logging implementation."""
    agent = _current_agent()

    # Get Python logger
    log = logging.getLogger(f"agent.{agent.__class__.__name__}")
    log_func = getattr(log, level.lower())

    # Emit to Python logging
    log_func(message, extra=data)

    # TODO: Also emit to event log for tracing
    # This would require async support or queueing
