# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Retry logic with exponential backoff for LLM calls.

This module provides configurable retry behavior for handling transient
failures in LLM API calls (rate limits, timeouts, server errors).
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from nemo_oo_agents.unifiedllm.retry_config import RetryConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class EmptyContentError(Exception):
    """Raised when LLM returns empty content but has reasoning.

    Some reasoning models (e.g., NVIDIA NIM nemotron, gpt-oss-20b) may return
    reasoning_content but null/empty content. This exception enables retry
    logic to handle these cases.
    """

    def __init__(self, reasoning: str | None = None):
        self.reasoning = reasoning
        super().__init__(
            f"Empty content with reasoning: {reasoning[:100]}..." if reasoning else "Empty content"
        )


def _calculate_delay(
    attempt: int,
    config: RetryConfig,
    is_rate_limit: bool = False,
) -> float:
    """Calculate delay with exponential backoff and jitter."""
    if is_rate_limit:
        base = config.rate_limit_base_delay
        exp_base = config.rate_limit_backoff_base
    else:
        base = config.base_delay
        exp_base = config.exponential_base

    # Exponential backoff
    delay = base * (exp_base**attempt)

    # Cap at max delay
    delay = min(delay, config.max_delay)

    # Add jitter
    jitter = delay * config.jitter_factor * random.random()
    delay += jitter

    return delay


def _is_retryable_error(error: Exception, config: RetryConfig) -> tuple[bool, bool]:
    """
    Check if an error is retryable.

    Returns:
        Tuple of (is_retryable, is_rate_limit)
    """
    # Check for empty content error (if enabled)
    if isinstance(error, EmptyContentError) and config.retry_on_empty_content:
        return True, False

    error_str = str(error).lower()

    # Check for rate limit (429)
    if "status 429" in error_str or "rate limit" in error_str:
        return True, True

    # Check for retryable status codes
    for code in config.retryable_status_codes:
        if f"status {code}" in error_str:
            return True, False

    # Check for timeout errors
    if isinstance(error, config.retryable_exceptions):
        return True, False

    if "timeout" in error_str or "timed out" in error_str:
        return True, False

    # Check for connection errors
    if "connection" in error_str and ("reset" in error_str or "refused" in error_str):
        return True, False

    return False, False


async def with_retry(
    func: Callable[..., Any],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> Any:
    """
    Execute an async function with retry logic.

    Args:
        func: Async function to call
        *args: Positional arguments for func
        config: Retry configuration (uses defaults if None)
        **kwargs: Keyword arguments for func

    Returns:
        Result of func

    Raises:
        The last exception if all retries are exhausted
    """
    config = config or RetryConfig()
    last_error: Exception | None = None
    attempt = 0

    # Calculate max attempts: normal retries + potential rate limit extra retries
    max_total_attempts = config.max_retries + 1 + config.rate_limit_extra_retries

    while attempt < max_total_attempts:
        try:
            return await func(*args, **kwargs)

        except asyncio.CancelledError:
            # aiohttp raises CancelledError (a BaseException) when its
            # ClientTimeout.total fires. Treat it as a retryable timeout
            # so the call gets another chance. If the cancellation came
            # from a real task-level timeout, the retry will be cancelled
            # immediately too, so this is safe.
            last_error = TimeoutError("asyncio.CancelledError (likely HTTP timeout)")
            is_retryable = True
            is_rate_limit = False
            if attempt >= config.max_retries:
                raise last_error from None
            delay = _calculate_delay(attempt, config, is_rate_limit=False)
            logger.warning(
                f"Retry {attempt + 1}/{config.max_retries + 1}: CancelledError (HTTP timeout). Waiting {delay:.1f}s"
            )
            if config.on_retry:
                config.on_retry(attempt + 1, last_error, delay)
            await asyncio.sleep(delay)
            attempt += 1
            continue

        except Exception as e:
            last_error = e
            is_retryable, is_rate_limit = _is_retryable_error(e, config)

            # Check if we should retry
            if not is_retryable:
                raise

            # For rate limits, we allow extra retries
            if is_rate_limit:
                # Rate limits can retry up to max_retries + rate_limit_extra_retries
                total_rate_limit_allowed = config.max_retries + config.rate_limit_extra_retries
                if attempt >= total_rate_limit_allowed:
                    raise
            else:
                # Non-rate-limit errors only get normal retries
                if attempt >= config.max_retries:
                    raise

            # Calculate delay
            delay = _calculate_delay(attempt, config, is_rate_limit)

            # Determine max retries for logging
            max_for_logging = (
                config.max_retries + config.rate_limit_extra_retries
                if is_rate_limit
                else config.max_retries
            )

            # Log retry
            logger.warning(
                f"Retry {attempt + 1}/{max_for_logging + 1}: {type(e).__name__}: {str(e)[:100]}... Waiting {delay:.1f}s"
            )

            # Call retry callback if provided
            if config.on_retry:
                config.on_retry(attempt + 1, e, delay)

            await asyncio.sleep(delay)
            attempt += 1

    # Should not reach here, but just in case
    if last_error:
        raise last_error
    raise RuntimeError("Retry loop completed without result or error")


def sync_retry(
    func: Callable[..., Any],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> Any:
    """
    Execute a sync function with retry logic.

    Args:
        func: Sync function to call
        *args: Positional arguments for func
        config: Retry configuration (uses defaults if None)
        **kwargs: Keyword arguments for func

    Returns:
        Result of func

    Raises:
        The last exception if all retries are exhausted
    """
    config = config or RetryConfig()
    last_error: Exception | None = None
    attempt = 0

    # Calculate max attempts: normal retries + potential rate limit extra retries
    max_total_attempts = config.max_retries + 1 + config.rate_limit_extra_retries

    while attempt < max_total_attempts:
        try:
            return func(*args, **kwargs)

        except Exception as e:
            last_error = e
            is_retryable, is_rate_limit = _is_retryable_error(e, config)

            # Check if we should retry
            if not is_retryable:
                raise

            # For rate limits, we allow extra retries
            if is_rate_limit:
                total_rate_limit_allowed = config.max_retries + config.rate_limit_extra_retries
                if attempt >= total_rate_limit_allowed:
                    raise
            else:
                # Non-rate-limit errors only get normal retries
                if attempt >= config.max_retries:
                    raise

            # Calculate delay
            delay = _calculate_delay(attempt, config, is_rate_limit)

            # Determine max retries for logging
            max_for_logging = (
                config.max_retries + config.rate_limit_extra_retries
                if is_rate_limit
                else config.max_retries
            )

            # Log retry
            logger.warning(
                f"Retry {attempt + 1}/{max_for_logging + 1}: {type(e).__name__}: {str(e)[:100]}... Waiting {delay:.1f}s"
            )

            # Call retry callback if provided
            if config.on_retry:
                config.on_retry(attempt + 1, e, delay)

            time.sleep(delay)
            attempt += 1

    # Should not reach here, but just in case
    if last_error:
        raise last_error
    raise RuntimeError("Retry loop completed without result or error")


class RetryingWrapper:
    """
    Wrapper that adds retry logic to any async callable.

    Example:
        client = CompletionClient(model="gpt-4o-mini")
        retrying_client = RetryingWrapper(client.acall, RetryConfig(max_retries=5))
        response = await retrying_client(messages=[...])
    """

    def __init__(
        self,
        func: Callable[..., Any],
        config: RetryConfig | None = None,
    ):
        self.func = func
        self.config = config or RetryConfig()

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return await with_retry(self.func, *args, config=self.config, **kwargs)
