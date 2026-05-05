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
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from nemo_oo_agents.config.truncation_config import FormatConfig

from nemo_oo_agents.context_blocks.events import ToolCallEvent
from nemo_oo_agents.context_blocks.formatter import (
    FORMAT_PLAIN,
    FORMAT_XML,
    BlockFormatter,
    ProviderFormatter,
    _markdown_message_content,
    _xml_message_content,
)
from nemo_oo_agents.context_blocks.models import (
    BlockMetadata,
    ContextWindowStats,
    RenderedMessage,
    ResolvedBlock,
    Role,
)
from nemo_oo_agents.context_blocks.utils import camel_to_snake


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


def _apply_context_total_limit(
    blocks: list[ResolvedBlock],
    total_limit: int,
    count_fn: Callable[[str], int],
) -> tuple[list[ResolvedBlock], int]:
    """Drop context blocks until total content fits within budget.

    Two-pass strategy: drop user blocks (``self.context``) from the end first,
    then drop remaining blocks from the end until the total fits. Appends a
    marker-family ``<context_blocks_evicted ...>`` block so the LLM knows what
    was dropped, plus actionable guidance for the agent author. Never mutates
    input.
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
    char_total = sum(size for _, _, size in dropped)
    dropped_keys = [key for key, _, _ in dropped]

    # Marker-family eviction notice + actionable guidance for the agent author.
    marker_lines = [
        f"<context_blocks_evicted len={len(dropped)}, "
        f"char_total={char_total}, "
        f"dropped_keys={dropped_keys!r}>",
        "",
        "To free up context space:",
        "- Summarize large data: self.context['key'] = summary_str",
        "- Remove blocks: self.context.pop('key')",
    ]

    summary_block = ResolvedBlock(
        key="context_blocks_evicted",
        content="\n".join(marker_lines),
        role=Role.SYSTEM,
        metadata=BlockMetadata(truncated=True),
    )

    return [*surviving, summary_block], len(to_drop)


def _apply_event_total_limit(
    blocks: list[ResolvedBlock],
    total_limit: int,
    count_fn: Callable[[str], int],
    min_preserved_events: int = 5,
) -> tuple[list[ResolvedBlock], int]:
    """Drop oldest events until total content fits within budget.

    Always preserves the last ``min_preserved_events`` events — the model's
    current Task / recent reasoning is what it needs to make the next decision.
    When eviction fires, prepends a marker-family
    ``<events_evicted len=N, char_total=X, dropped_kinds={...}>`` block so the
    LLM can see what was dropped instead of silently starting mid-trajectory.
    """
    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0

    # Eviction floor: don't touch the last `min_preserved_events`.
    eviction_ceiling = max(0, len(blocks) - min_preserved_events)

    start = 0
    dropped_kinds: dict[str, int] = {}
    char_total = 0
    while start < eviction_ceiling and total > total_limit:
        block = blocks[start]
        size = count_fn(block.content)
        total -= size
        char_total += size
        if block.event is not None:
            kind = camel_to_snake(type(block.event).__name__)
            dropped_kinds[kind] = dropped_kinds.get(kind, 0) + 1
        start += 1

    if start == 0:
        return blocks, 0

    surviving = blocks[start:]

    # Marker shape mirrors L1 family: <events_evicted len=N, char_total=X, dropped_kinds={...}>
    # so the LLM treats it as a recognizable elision marker, not noise.
    kinds_str = "{" + ", ".join(f"{k}: {v}" for k, v in sorted(dropped_kinds.items())) + "}"
    marker = ResolvedBlock(
        key="events_evicted",
        content=(
            f"<events_evicted len={start}, char_total={char_total}, dropped_kinds={kinds_str}>"
        ),
        role=Role.SYSTEM,
        metadata=BlockMetadata(truncated=True),
    )

    return [marker, *surviving], start


def render_context(
    blocks: list[ResolvedBlock],
    *,
    block_formatter: BlockFormatter,
    provider_formatter: ProviderFormatter,
    context_limit: int | None = None,
    event_limit: int | None = None,
    count_tokens: Callable[[str], int] | None = None,
    pre_format_limit: int | None = None,
    event_format: "FormatConfig | None" = None,
    min_preserved_events: int = 5,
    model_context_window: int | None = None,
) -> RenderResult:
    """Render resolved blocks into provider-specific output with utilization stats.

    Never mutates input blocks. Per-block head/tail truncation has been removed
    — content passes through verbatim. Total-context / total-event eviction
    (``context_limit`` / ``event_limit``) drops whole blocks when over budget.

    ``event_format`` carries the structural bounds (max_string / max_length /
    max_depth) for event-field rendering at trajectory build time. The
    block_formatter's ``format_event`` is called with these bounds so that
    nested string fields within events are bounded (otherwise pformat's
    structured-instance fallback caps strings at 150 chars).
    """
    if count_tokens is None and (context_limit is not None or event_limit is not None):
        raise ValueError(
            "max_context_tokens / max_event_tokens require a token counter. "
            "Pass count_tokens=llm.count_tokens to render_context()."
        )

    count_fn: Callable[[str], int] = count_tokens if count_tokens is not None else len

    # Partition for truncation only.
    system_blocks = [b for b in blocks if b.role == Role.SYSTEM]
    message_blocks = [b for b in blocks if b.role != Role.SYSTEM]

    # Pre-serialize non-tool events so their content can be measured.
    # ToolCallEvents stay at content="" — the BlockFormatter handles them structurally.
    serialized_messages: list[ResolvedBlock] = []
    for block in message_blocks:
        if block.event is not None and not isinstance(block.event, ToolCallEvent):
            content = block_formatter.format_event(
                block.event,
                max_chars=pre_format_limit,
                event_format=event_format,
            )
            block = block.model_copy(update={"content": content})
        serialized_messages.append(block)
    message_blocks = serialized_messages

    # Total-context / total-event eviction: drop whole blocks when over budget.
    context_blocks_dropped = 0
    if context_limit is not None:
        system_blocks, context_blocks_dropped = _apply_context_total_limit(
            system_blocks, context_limit, count_fn
        )

    events_dropped = 0
    if event_limit is not None:
        message_blocks, events_dropped = _apply_event_total_limit(
            message_blocks, event_limit, count_fn, min_preserved_events=min_preserved_events
        )

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
