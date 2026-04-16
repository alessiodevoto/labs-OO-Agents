# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render pre-resolved context blocks into provider-specific output.

Dead simple: takes a list of ResolvedBlocks, partitions by role,
formats via BlockFormatter + ProviderFormatter, handles truncation.

No eval function. No expression evaluation. All content is pre-resolved.

IMPORTANT: render_context() never mutates its input blocks. Truncation and
message formatting produce new ResolvedBlock instances via model_copy().
"""

from collections.abc import Callable
from typing import Any, NamedTuple

from context_blocks.events import ToolCallEvent
from context_blocks.formatter import FORMAT_PLAIN, FORMAT_XML, BlockFormatter, ProviderFormatter
from context_blocks.models import BlockMetadata, ContextWindowStats, ResolvedBlock, Role
from context_blocks.utils import camel_to_snake, truncate_content


class RenderResult(NamedTuple):
    """Result of render_context(): provider output + utilization stats."""

    output: Any
    stats: ContextWindowStats


def format_message_content(block: ResolvedBlock, format_type: str) -> str:
    """Wrap message block content with metadata (XML tags or Markdown headers).

    Called for non-tool-call message blocks that have expr/tag metadata,
    so the LLM sees evaluatable expression references.

    Args:
        block: A resolved message block with content and metadata.
        format_type: One of "xml", "markdown", or "plain".

    Returns:
        For "xml": content wrapped in an XML element with expr/tag attributes.
        For "plain": content wrapped in event-type or role XML tags (no expr=).
        For "markdown" (and any other value): content under a Markdown header.
    """
    if format_type == FORMAT_XML:
        role_label = block.role.value + "_message"
        attr_parts = []
        if block.metadata.expr:
            attr_parts.append(f'expr="{block.metadata.expr}"')
        if block.metadata.tag:
            attr_parts.append(f'tag="{block.metadata.tag}"')
        attrs = (" " + " ".join(attr_parts)) if attr_parts else ""
        return f"<{role_label}{attrs}>\n{block.content}\n</{role_label}>"
    elif format_type == FORMAT_PLAIN:
        if block.event is not None:
            event_tag = camel_to_snake(type(block.event).__name__)
            tag_attr = f' tag="{block.metadata.tag}"' if block.metadata.tag else ""
            return f"<{event_tag}{tag_attr}>\n{block.content}\n</{event_tag}>"
        elif block.metadata.tag:
            role_label = block.role.value + "_message"
            return f'<{role_label} tag="{block.metadata.tag}">\n{block.content}\n</{role_label}>'
        return block.content
    else:
        role_label = block.role.value.replace("_", " ").title() + " Message"
        meta_parts = []
        if block.metadata.expr:
            meta_parts.append(f'"expr": "{block.metadata.expr}"')
        if block.metadata.tag:
            meta_parts.append(f'"tag": "{block.metadata.tag}"')
        inline_meta = (" `{" + ", ".join(meta_parts) + "}`") if meta_parts else ""
        return f"### {role_label}{inline_meta}\n\n{block.content}"


def _truncate_blocks(
    blocks: list[ResolvedBlock],
    per_block_limit: int | None,
    format_type: str,
) -> list[ResolvedBlock]:
    """Apply per-block truncation to a list of blocks.

    Returns new list — never mutates input.
    """
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

    Two-pass strategy:
    1. First, drop user blocks (self.context) from the end — these are
       the blocks the LLM can manage (summarize, remove).
    2. If still over budget, drop remaining blocks from the end
       (strategy/decorator/scoped overrides, then framework blocks).

    This preserves framework essentials and strategy configuration,
    sacrificing user-populated context data first.

    Returns (new_blocks, dropped_count) — never mutates input.
    """
    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0

    to_drop: set[int] = set()
    dropped: list[tuple[str, str | None, int]] = []

    # Pass 1: drop user blocks from the end first
    for i in range(len(blocks) - 1, -1, -1):
        if total <= total_limit:
            break
        if blocks[i].metadata.user_block:
            size = count_fn(blocks[i].content)
            total -= size
            to_drop.add(i)
            dropped.append((blocks[i].key, blocks[i].metadata.expr, size))

    # Pass 2: if still over budget, drop non-user blocks from the end
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

    # Summary block so the LLM knows what was dropped
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
    """Drop oldest events until total content fits within budget.

    Events are ordered chronologically — oldest first. When over budget,
    we drop from the front (oldest) to preserve recent context.

    Returns (new_blocks, dropped_count) — never mutates input.
    """
    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0

    # Drop oldest events (from the front) until we fit.
    # Use a running total to avoid O(n²) recomputation.
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
) -> RenderResult:
    """Render resolved blocks into provider-specific output with utilization stats.

    Never mutates input blocks — truncation and message formatting
    produce new instances via model_copy().

    Args:
        blocks: Pre-resolved blocks with content ready for rendering.
        block_formatter: How to format system prompt blocks (XML, Markdown).
        provider_formatter: How to assemble for provider (OpenAI, Anthropic).
        block_limit: Optional character limit per individual block (context or event).
        context_limit: Optional total budget for all system blocks (in tokens if
            count_tokens is provided, otherwise chars). When exceeded, lowest-priority
            blocks are dropped first and a summary block is added.
        event_limit: Optional total budget for all events (in tokens if count_tokens
            is provided, otherwise chars). When exceeded, oldest events are dropped.
        count_tokens: Token counting function. Required if context_limit or event_limit
            is set. When None and no token limits are set, char-based counting is used.
        pre_format_limit: Hard character cap passed to block_formatter.format_event()
            before block-level truncation.  Comes from
            TruncationConfig.max_block_chars (single pipeline — same cap for both).
            None uses the formatter default.

    Returns:
        RenderResult with .output (provider-specific: list[dict] for OpenAI,
        dict for Anthropic) and .stats (ContextWindowStats).
    """
    if count_tokens is None and (context_limit is not None or event_limit is not None):
        raise ValueError(
            "max_context_tokens / max_event_tokens require a token counter. "
            "Pass count_tokens=llm.count_tokens to render_context()."
        )

    count_fn: Callable[[str], int] = count_tokens if count_tokens is not None else len

    formatter_type = block_formatter.format_type

    # Partition blocks by role
    system_blocks = [b for b in blocks if b.role == Role.SYSTEM]
    message_blocks = [b for b in blocks if b.role != Role.SYSTEM]

    # Truncate individual system blocks
    system_blocks = _truncate_blocks(system_blocks, block_limit, formatter_type)

    # Apply context total limit — drop blocks from the end first
    context_blocks_dropped = 0
    if context_limit is not None:
        system_blocks, context_blocks_dropped = _apply_context_total_limit(system_blocks, context_limit, count_fn)

    # Format system blocks into context string
    context_str = block_formatter.format(system_blocks)

    # Serialize non-tool event content before truncation.
    # Events arrive with content="" (deferred from _phase_events). If a block has both
    # event and pre-existing content, format_event() output takes precedence — the
    # original content is intentionally replaced.
    # ToolCallEvent blocks stay at content="" — handled by ProviderFormatter via block.event.
    serialized_messages: list[ResolvedBlock] = []
    for block in message_blocks:
        if block.event is not None and not isinstance(block.event, ToolCallEvent):
            kwargs = {} if pre_format_limit is None else {"max_chars": pre_format_limit}
            content = block_formatter.format_event(block.event, **kwargs)
            block = block.model_copy(update={"content": content})
        serialized_messages.append(block)
    message_blocks = serialized_messages

    # Truncate individual message blocks
    message_blocks = _truncate_blocks(message_blocks, block_limit, formatter_type)

    # Apply event total limit — drop oldest events first
    events_dropped = 0
    if event_limit is not None:
        message_blocks, events_dropped = _apply_event_total_limit(message_blocks, event_limit, count_fn)

    # Compute stats after truncation, before provider formatting
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
        context_blocks_dropped=context_blocks_dropped,
        events_dropped=events_dropped,
    )

    # Pre-format message blocks with metadata wrapping (before provider assembly)
    # Tool-call blocks are skipped — they carry the original event, not string content.
    formatted_messages: list[ResolvedBlock] = []
    for block in message_blocks:
        if not isinstance(block.event, ToolCallEvent) and (block.metadata.expr or block.metadata.tag):
            content = format_message_content(block, formatter_type)
            block = block.model_copy(update={"content": content})
        formatted_messages.append(block)

    # Assemble system prompt + messages via provider formatter
    output = provider_formatter.format(context_str, formatted_messages)
    return RenderResult(output=output, stats=stats)
