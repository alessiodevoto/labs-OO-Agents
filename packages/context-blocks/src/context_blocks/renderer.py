# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render pre-resolved context blocks into provider-specific output.

Pipeline:

1. Partition blocks by role for truncation.
2. Pre-serialize non-tool event content so it can be measured and trimmed.
3. Apply block-level and total-budget truncation.
4. Hand the full ordered list of blocks to ``block_formatter`` — it produces a
   neutral ``list[RenderedMessage]`` covering system + events + any extra
   trailing messages the formatter chooses to emit.
5. Hand the neutral list to ``provider_formatter`` to reshape into the
   provider-specific wire format.

``render_context()`` never mutates its input blocks — truncation and
serialization produce new :class:`ResolvedBlock` instances via ``model_copy()``.
"""

from collections.abc import Callable
from typing import Any, NamedTuple

from context_blocks.events import ToolCallEvent
from context_blocks.formatter import (
    FORMAT_PLAIN,
    FORMAT_XML,
    BlockFormatter,
    ProviderFormatter,
    _markdown_message_content,
    _xml_message_content,
)
from context_blocks.models import (
    BlockMetadata,
    ContextWindowStats,
    RenderedMessage,
    ResolvedBlock,
    Role,
)
from context_blocks.utils import camel_to_snake, truncate_content


class RenderResult(NamedTuple):
    """Result of :func:`render_context`: provider output + utilization stats.

    ``messages`` is the neutral ``list[RenderedMessage]`` produced by the
    BlockFormatter *after* truncation and *before* provider formatting.
    Block-aware formatters populate ``RenderedMessage.parts`` on this list,
    which the journal publisher walks to build a content-addressed skeleton.
    """

    output: Any
    stats: ContextWindowStats
    messages: list[RenderedMessage]


def format_message_content(block: ResolvedBlock, format_type: str) -> str:
    """Wrap an event block's content with a role tag and metadata.

    Utility for callers that want to reuse the stock XML / Markdown / plain
    message wrapping outside the renderer pipeline (e.g. summarization agents
    rendering events into an LLM prompt).
    """
    if format_type == FORMAT_XML:
        return _xml_message_content(block)
    if format_type == FORMAT_PLAIN:
        if block.event is not None:
            event_tag = camel_to_snake(type(block.event).__name__)
            tag_attr = f' tag="{block.metadata.tag}"' if block.metadata.tag else ""
            return f"<{event_tag}{tag_attr}>\n{block.content}\n</{event_tag}>"
        if block.metadata.tag:
            role_label = f"{block.role.value}_message"
            return f'<{role_label} tag="{block.metadata.tag}">\n{block.content}\n</{role_label}>'
        return block.content
    # Markdown and any other value
    return _markdown_message_content(block)


def _truncate_blocks(
    blocks: list[ResolvedBlock],
    per_block_limit: int | None,
    format_type: str,
) -> list[ResolvedBlock]:
    """Apply per-block truncation to a list of blocks. Returns a new list."""
    if per_block_limit is None:
        return blocks

    result: list[ResolvedBlock] = []
    for block in blocks:
        content, was_truncated = truncate_content(block.content, per_block_limit, format_type)
        if was_truncated:
            block = block.model_copy(
                update={
                    "content": content,
                    "metadata": block.metadata.model_copy(update={"truncated": True}),
                }
            )
        result.append(block)

    return result


def _apply_context_total_limit(
    blocks: list[ResolvedBlock],
    total_limit: int,
    count_fn: Callable[[str], int],
) -> tuple[list[ResolvedBlock], int]:
    """Drop context blocks until total content fits within budget.

    Two-pass strategy: drop user blocks (``self.context``) from the end first,
    then drop remaining blocks from the end until the total fits. Appends a
    summary block so the LLM knows what was dropped. Never mutates input.
    """
    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0

    to_drop: set[int] = set()
    dropped: list[tuple[str, str | None, int]] = []

    for i in range(len(blocks) - 1, -1, -1):
        if total <= total_limit:
            break
        if blocks[i].metadata.user_block:
            size = count_fn(blocks[i].content)
            total -= size
            to_drop.add(i)
            dropped.append((blocks[i].key, blocks[i].metadata.expr, size))

    if total > total_limit:
        for i in range(len(blocks) - 1, -1, -1):
            if total <= total_limit:
                break
            if i not in to_drop:
                size = count_fn(blocks[i].content)
                total -= size
                to_drop.add(i)
                dropped.append((blocks[i].key, blocks[i].metadata.expr, size))

    surviving = [b for i, b in enumerate(blocks) if i not in to_drop]

    lines = ["WARNING: The following context blocks were dropped to fit within the context budget:"]
    for key, expr, size in dropped:
        expr_str = f" (expr: {expr})" if expr else ""
        lines.append(f"- {key}{expr_str}: {size:,} chars")
    lines.append("")
    lines.append("To free up context space:")
    lines.append("- Summarize large data: self.context['key'] = summary_str")
    lines.append("- Remove blocks: self.context.pop('key')")

    summary_block = ResolvedBlock(
        key="truncation_notice",
        content="\n".join(lines),
        role=Role.SYSTEM,
        metadata=BlockMetadata(truncated=True),
    )

    return [*surviving, summary_block], len(to_drop)


def _apply_event_total_limit(
    blocks: list[ResolvedBlock],
    total_limit: int,
    count_fn: Callable[[str], int],
) -> tuple[list[ResolvedBlock], int]:
    """Drop oldest events until total content fits within budget."""
    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0

    start = 0
    while start < len(blocks) and total > total_limit:
        total -= count_fn(blocks[start].content)
        start += 1

    return blocks[start:], start


def render_context(
    blocks: list[ResolvedBlock],
    *,
    block_formatter: BlockFormatter,
    provider_formatter: ProviderFormatter,
    block_limit: int | None = None,
    context_limit: int | None = None,
    event_limit: int | None = None,
    count_tokens: Callable[[str], int] | None = None,
    pre_format_limit: int | None = None,
    model_context_window: int | None = None,
) -> RenderResult:
    """Render resolved blocks into provider-specific output with utilization stats.

    Never mutates input blocks. Truncation and event serialization produce new
    ``ResolvedBlock`` instances via ``model_copy()``.
    """
    if count_tokens is None and (context_limit is not None or event_limit is not None):
        raise ValueError(
            "max_context_tokens / max_event_tokens require a token counter. "
            "Pass count_tokens=llm.count_tokens to render_context()."
        )

    count_fn: Callable[[str], int] = count_tokens if count_tokens is not None else len
    formatter_type = block_formatter.format_type

    # Partition for truncation only.
    system_blocks = [b for b in blocks if b.role == Role.SYSTEM]
    message_blocks = [b for b in blocks if b.role != Role.SYSTEM]

    # Pre-serialize non-tool events so their content can be measured and trimmed.
    # ToolCallEvents stay at content="" — the BlockFormatter handles them structurally.
    serialized_messages: list[ResolvedBlock] = []
    for block in message_blocks:
        if block.event is not None and not isinstance(block.event, ToolCallEvent):
            kwargs = {} if pre_format_limit is None else {"max_chars": pre_format_limit}
            content = block_formatter.format_event(block.event, **kwargs)
            block = block.model_copy(update={"content": content})
        serialized_messages.append(block)
    message_blocks = serialized_messages

    # Per-block and total truncation.
    system_blocks = _truncate_blocks(system_blocks, block_limit, formatter_type)
    context_blocks_dropped = 0
    if context_limit is not None:
        system_blocks, context_blocks_dropped = _apply_context_total_limit(system_blocks, context_limit, count_fn)

    message_blocks = _truncate_blocks(message_blocks, block_limit, formatter_type)
    events_dropped = 0
    if event_limit is not None:
        message_blocks, events_dropped = _apply_event_total_limit(message_blocks, event_limit, count_fn)

    # Stats — computed on truncated content, before formatter sees it.
    context_blocks_tokens = sum(count_fn(b.content) for b in system_blocks)
    events_tokens = sum(count_fn(b.content) for b in message_blocks)
    stats = ContextWindowStats(
        context_blocks_tokens=context_blocks_tokens,
        context_blocks_count=len(system_blocks),
        events_tokens=events_tokens,
        events_count=len(message_blocks),
        total_tokens=context_blocks_tokens + events_tokens,
        max_context_tokens=context_limit,
        max_event_tokens=event_limit,
        model_context_window=model_context_window,
        context_blocks_dropped=context_blocks_dropped,
        events_dropped=events_dropped,
    )

    # Neutral message list → provider wire format.
    messages = block_formatter.format([*system_blocks, *message_blocks])
    output = provider_formatter.format(messages)
    return RenderResult(output=output, stats=stats, messages=messages)
