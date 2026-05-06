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

    ``events_truncated`` is a render-time-synthesized ``ContextTruncated``
    event holding the eviction details (kinds histogram, char_total, tags),
    or ``None`` if no events were evicted. The actor uses this to emit the
    same event into the event_manager so future turns and agent introspection
    see the eviction record.
    """

    output: Any
    stats: ContextWindowStats
    messages: list[RenderedMessage]
    events_truncated: Any = None  # ContextTruncated | None — Any to avoid import cycle


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
    """Mark over-budget context blocks as EVICTED in-place.

    Two-pass strategy: select user blocks (``self.context``) from the end first,
    then remaining blocks from the end until the total fits. Selected blocks
    keep their original key/position and render an EVICTED label with per-block
    size stats.
    """
    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0

    to_evict: set[int] = set()
    evicted_sizes: dict[int, int] = {}

    for i in range(len(blocks) - 1, -1, -1):
        if total <= total_limit:
            break
        if blocks[i].metadata.user_block:
            size = count_fn(blocks[i].content)
            total -= size
            to_evict.add(i)
            evicted_sizes[i] = size

    if total > total_limit:
        for i in range(len(blocks) - 1, -1, -1):
            if total <= total_limit:
                break
            if i not in to_evict:
                size = count_fn(blocks[i].content)
                total -= size
                to_evict.add(i)
                evicted_sizes[i] = size

    rendered: list[ResolvedBlock] = []
    for i, block in enumerate(blocks):
        if i not in to_evict:
            rendered.append(block)
            continue
        size = evicted_sizes.get(i, count_fn(block.content))
        msg = f"EVICTED: over context budget (block_tokens={size:,})"
        rendered.append(
            block.model_copy(
                update={
                    "content": msg,
                    "metadata": block.metadata.model_copy(update={"truncated": True}),
                }
            )
        )

    return rendered, len(to_evict)


def _apply_event_total_limit(
    blocks: list[ResolvedBlock],
    total_limit: int,
    count_fn: Callable[[str], int],
    min_preserved_events: int = 5,
) -> tuple[list[ResolvedBlock], int, Any]:
    """Drop oldest evictable events until total content fits within budget.

    Two preservation rules apply (events matching either are NOT evicted):

    - **Recent-N anchor**: the last ``min_preserved_events`` events always
      survive. The model needs its recent reasoning to make the next decision.
    - **Latest-Task anchor**: the most recent ``Task`` event always survives.
      The original instruction is what the agent is trying to execute; losing
      it leaves the model with no goal.

    When eviction fires, prepends a ``Summary`` event marker — the same shape
    the LLM sees from ``events.collapse()`` — so the rendered marker is a
    recognizable form rather than a one-off syntax. The marker is
    ``role=USER`` (inline in the timeline at the gap, not bundled with the
    system prompt).
    """
    # Lazy import to avoid a circular dependency between context_blocks and the
    # framework's event types (nemo_oo_agents.events imports from context_blocks).
    from nemo_oo_agents.context_blocks.utils import truncating_pformat
    from nemo_oo_agents.events import ContextTruncated, Task

    total = sum(count_fn(b.content) for b in blocks)
    if total <= total_limit:
        return blocks, 0, None

    # Build the set of indices that must NOT be evicted.
    preserved: set[int] = set()
    # Recent-N anchor.
    preserved.update(range(max(0, len(blocks) - min_preserved_events), len(blocks)))
    # Latest-Task anchor: walk back-to-front, preserve the first Task we find.
    for i in range(len(blocks) - 1, -1, -1):
        if isinstance(blocks[i].event, Task):
            preserved.add(i)
            break

    # Evict oldest-first across non-preserved indices until budget fits.
    dropped_indices: list[int] = []
    dropped_kinds: dict[str, int] = {}
    char_total = 0
    for i in range(len(blocks)):
        if total <= total_limit:
            break
        if i in preserved:
            continue
        block = blocks[i]
        size = count_fn(block.content)
        total -= size
        char_total += size
        if block.event is not None:
            kind = camel_to_snake(type(block.event).__name__)
            dropped_kinds[kind] = dropped_kinds.get(kind, 0) + 1
        dropped_indices.append(i)

    if not dropped_indices:
        return blocks, 0, None

    drop_set = set(dropped_indices)
    dropped_blocks = [blocks[i] for i in dropped_indices]
    surviving = [b for i, b in enumerate(blocks) if i not in drop_set]

    # Compute (min, max) of dropped tag sequence numbers for the marker's
    # replaced_range. Falls back to (0, count) if tags aren't numeric.
    dropped_tags = [b.metadata.tag for b in dropped_blocks if b.metadata.tag]
    if dropped_tags:
        try:
            nums = []
            for t in dropped_tags:
                parts = t.split("..")
                nums.append(int(parts[0]))
                nums.append(int(parts[-1]))
            range_start, range_end = min(nums), max(nums)
        except ValueError:
            range_start, range_end = 0, len(dropped_blocks)
    else:
        range_start, range_end = 0, len(dropped_blocks)

    truncated = ContextTruncated(
        replaced_range=(range_start, range_end),
        dropped_count=len(dropped_blocks),
        char_total=char_total,
        dropped_kinds=dict(sorted(dropped_kinds.items())),
        dropped_tags=dropped_tags,
    )

    marker = ResolvedBlock(
        key="context_truncated",
        # Pre-render the event to a string (we're past render_context's
        # pre-serialization step; surviving blocks already have content set).
        content=truncating_pformat(truncated),
        # role=USER so the marker appears inline in the timeline (where the
        # gap actually is) rather than bundling with the system prompt.
        role=Role.USER,
        metadata=BlockMetadata(truncated=True),
        event=truncated,
    )

    return [marker, *surviving], len(dropped_indices), truncated


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
    event_limit_includes_context: bool = False,
    model_context_window: int | None = None,
) -> RenderResult:
    """Render resolved blocks into provider-specific output with utilization stats.

    Never mutates input blocks. Per-block head/tail truncation has been removed
    — content passes through verbatim. Context blocks over budget are marked
    EVICTED in place; events over budget are evicted oldest-first.

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
            content = block_formatter.format_event(block.event, event_format=event_format)
            block = block.model_copy(update={"content": content})
        serialized_messages.append(block)
    message_blocks = serialized_messages

    # Total-context eviction: mark over-budget blocks EVICTED in place.
    context_blocks_dropped = 0
    if context_limit is not None:
        system_blocks, context_blocks_dropped = _apply_context_total_limit(
            system_blocks, context_limit, count_fn
        )

    # Event budget can optionally be interpreted as a TOTAL input budget
    # (context + events + response reserve). In that mode, subtract the true
    # post-truncation context-block cost so event eviction uses the remaining
    # budget rather than the raw global cap.
    context_blocks_tokens = sum(count_fn(b.content) for b in system_blocks)
    applied_event_limit = event_limit
    if applied_event_limit is not None and event_limit_includes_context:
        applied_event_limit = max(0, applied_event_limit - context_blocks_tokens)

    events_dropped = 0
    events_truncated_event: Any = None
    if applied_event_limit is not None:
        message_blocks, events_dropped, events_truncated_event = _apply_event_total_limit(
            message_blocks,
            applied_event_limit,
            count_fn,
            min_preserved_events=min_preserved_events,
        )

    # Stats — computed on truncated content, before formatter sees it.
    events_tokens = sum(count_fn(b.content) for b in message_blocks)
    stats = ContextWindowStats(
        context_blocks_tokens=context_blocks_tokens,
        context_blocks_count=len(system_blocks),
        events_tokens=events_tokens,
        events_count=len(message_blocks),
        total_tokens=context_blocks_tokens + events_tokens,
        max_context_tokens=context_limit,
        max_event_tokens=applied_event_limit,
        model_context_window=model_context_window,
        context_blocks_dropped=context_blocks_dropped,
        events_dropped=events_dropped,
    )

    # Neutral message list → provider wire format.
    messages = block_formatter.format([*system_blocks, *message_blocks])
    output = provider_formatter.format(messages)
    return RenderResult(
        output=output,
        stats=stats,
        messages=messages,
        events_truncated=events_truncated_event,
    )
