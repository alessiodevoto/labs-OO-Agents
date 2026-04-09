# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Monkey patches for openinference-instrumentation-litellm bugs.

Fixes:
1. Bug where null/empty content causes entire JSON response to be logged
   in output.value instead of the actual content value.
2. Bug where tool_call.id is not captured in span attributes (despite being
   defined in OpenInference semantic conventions).
3. Missing reasoning_content capture for reasoning models (DeepSeek, o1, Nemotron, etc.)
4. Extract <think> tags from content for models that embed reasoning (Nemotron, QwQ)

Bug: https://github.com/Arize-ai/openinference/issues (to be filed)
Affected version: openinference-instrumentation-litellm v0.1.28+
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from openinference.semconv.trace import (
    MessageAttributes,
    OpenInferenceMimeTypeValues,
    SpanAttributes,
    ToolCallAttributes,
)
from opentelemetry import trace as trace_api
from opentelemetry.util.types import AttributeValue
from pydantic import BaseModel


def _extract_think_tags(content: str) -> tuple[str, str | None]:
    """Extract <think>...</think> tags from content.

    Returns (cleaned_content, reasoning) where:
    - cleaned_content is the content with think tags removed
    - reasoning is the extracted thinking content (or None if no tags found)

    Handles both complete tags and malformed tags (missing opening tag due to litellm bug).
    """
    if not content:
        return content, None

    # Pattern for complete <think>...</think> tags
    think_pattern = r"<think>(.*?)</think>"
    match = re.search(think_pattern, content, re.DOTALL)

    if match:
        reasoning = match.group(1).strip()
        cleaned = re.sub(think_pattern, "", content, flags=re.DOTALL).strip()
        return cleaned, reasoning

    # Handle malformed case: content starts with thinking and ends with </think>
    # (litellm bug strips opening <think> but leaves closing </think>)
    if "</think>" in content:
        parts = content.split("</think>", 1)
        if len(parts) == 2:
            reasoning = parts[0].strip()
            cleaned = parts[1].strip()
            return cleaned, reasoning

    return content, None


def _patched_set_output_message_value(span: trace_api.Span, result: Any) -> Any:
    """Fixed version of _set_output_message_value that handles null/empty content.

    The original bug: when content is None or "", the walrus operator evaluates
    to falsy, causing the condition to fail and fall through to dumping the
    entire JSON response.

    Fix: Check explicitly for None and use span.set_attribute directly
    (the _set_span_attribute helper filters out empty strings).

    Enhancement: Also capture reasoning_content for reasoning models.
    """
    from litellm.types.utils import Choices

    if result.choices and isinstance(result.choices[-1], Choices):
        message = result.choices[-1].message
        content = message.content
        # Use direct set_attribute to preserve empty strings
        # (the original _set_span_attribute helper filters them out)
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, content if content is not None else "")

        # Capture reasoning_content for reasoning models (DeepSeek, o1, Nemotron, etc.)
        # This is returned by models like deepseek-reasoner, o1-*, and some NVIDIA models
        reasoning_content = getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None)

        # Also extract <think> tags from content for models that embed reasoning
        # (Nemotron, QwQ, etc.) - these don't use reasoning_content field
        if not reasoning_content and content:
            _, think_reasoning = _extract_think_tags(content)
            if think_reasoning:
                reasoning_content = think_reasoning

        if reasoning_content:
            # Use a custom attribute since OpenInference doesn't have a standard one yet
            span.set_attribute("llm.reasoning_content", reasoning_content)
    else:
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, result.model_dump_json())
        span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.JSON.value)


# Store reference to original function for the tool_call.id patch
_original_get_attributes_from_message_param: Any = None


def _patched_get_attributes_from_message_param(
    message: Mapping[str, Any],
) -> Iterator[tuple[str, AttributeValue]]:
    """Fixed version that includes tool_call.id in span attributes.

    The original bug: The LiteLLM instrumentation captures tool_call.function.name
    and tool_call.function.arguments but NOT tool_call.id, even though:
    1. The OpenInference semantic conventions define TOOL_CALL_ID
    2. The core openinference library (_attributes.py) captures it correctly
    3. The LiteLLM-specific code reimplements this but forgot the ID

    Fix: Yield tool_call.id for each tool call in addition to function details.
    """
    # First, yield all attributes from the original function
    if _original_get_attributes_from_message_param is not None:
        yield from _original_get_attributes_from_message_param(message)

    # Convert to dict if needed (message might be a Message object for output messages)
    if isinstance(message, BaseModel):
        message = message.model_dump()

    # Then, add the missing tool_call.id attributes
    if (tool_calls := message.get("tool_calls")) and isinstance(tool_calls, Iterable):
        for tool_call_index, tool_call in enumerate(tool_calls):
            if isinstance(tool_call, Mapping) and (tool_call_id := tool_call.get("id")):
                yield (
                    f"{MessageAttributes.MESSAGE_TOOL_CALLS}.{tool_call_index}.{ToolCallAttributes.TOOL_CALL_ID}",
                    tool_call_id,
                )

    # Also add message.tool_call_id for tool result messages (role=tool)
    if (tool_call_id := message.get("tool_call_id")) and isinstance(tool_call_id, str):
        yield (MessageAttributes.MESSAGE_TOOL_CALL_ID, tool_call_id)


def apply_litellm_patch() -> None:
    """Apply monkey patches to fix litellm instrumentation bugs.

    This patches:
    1. _set_output_message_value - fixes null/empty content handling + adds reasoning_content
    2. _get_attributes_from_message_param - adds missing tool_call.id capture
    """
    global _original_get_attributes_from_message_param

    try:
        from openinference.instrumentation import litellm

        # Patch 1: Fix null/empty content handling
        litellm._set_output_message_value = _patched_set_output_message_value

        # Patch 2: Fix missing tool_call.id
        # Store the original function so our patch can call it
        # CRITICAL: Only patch if not already patched (prevents infinite recursion)
        if litellm._get_attributes_from_message_param is not _patched_get_attributes_from_message_param:
            _original_get_attributes_from_message_param = litellm._get_attributes_from_message_param
            litellm._get_attributes_from_message_param = _patched_get_attributes_from_message_param

    except (ImportError, AttributeError):
        # If litellm instrumentation is not available, silently skip
        pass
