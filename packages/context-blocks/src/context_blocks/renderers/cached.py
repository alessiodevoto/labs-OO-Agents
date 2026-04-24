# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cached renderer: partitions blocks by ``metadata.immutable`` for prefix caching.

Structure produced:

    (SYSTEM)    immutable blocks, stable across turns — cacheable prefix
    (events)    the full event history, append-only
    (USER)      trailing message wrapping volatile blocks in a ``<context>``
                envelope (merged into the last user event when one is
                trailing, so user/assistant alternation is preserved)

Implemented as a single :class:`CachedBlockFormatter`. Pair with any stock
provider formatter (``OpenAIProviderFormatter``, ``AnthropicProviderFormatter``);
no paired provider formatter is needed.

Decoration is minimal by design: no "you are an agent" prose, no format
description. Authors see the structure through the XML tags themselves.
"""

from context_blocks.events import ToolCallEvent
from context_blocks.formatter import (
    FORMAT_XML,
    BlockFormatter,
    FormatType,
    _event_block_to_messages,
    _xml_system_block,
)
from context_blocks.models import (
    BlockPart,
    MessagePart,
    RenderedMessage,
    ResolvedBlock,
    Role,
    TextPart,
)


def _partition(
    system_blocks: list[ResolvedBlock],
) -> tuple[list[ResolvedBlock], list[ResolvedBlock]]:
    """Split SYSTEM-role blocks into (immutable, volatile) by metadata flag."""
    immutable: list[ResolvedBlock] = []
    volatile: list[ResolvedBlock] = []
    for b in system_blocks:
        (immutable if b.metadata.immutable else volatile).append(b)
    return immutable, volatile


def _xml_concat(blocks: list[ResolvedBlock], separator: str = "\n\n") -> str:
    return separator.join(_xml_system_block(b) for b in blocks)


def _concat_parts(
    blocks: list[ResolvedBlock], separator: str = "\n\n"
) -> tuple[str, list[MessagePart]]:
    """Render ``blocks`` as XML and return (joined_content, parts) for journaling."""
    pieces = [_xml_system_block(b) for b in blocks]
    parts: list[MessagePart] = []
    for i, (block, rendered) in enumerate(zip(blocks, pieces, strict=True)):
        if i > 0:
            parts.append(TextPart(text=separator))
        parts.append(BlockPart(key=block.key, content=rendered))
    return separator.join(pieces), parts


class CachedBlockFormatter(BlockFormatter):
    """Partitions system blocks into an immutable prefix and a volatile suffix.

    The immutable half becomes a SYSTEM message at the head of the output. The
    volatile half, if any, is wrapped in a ``<context>`` envelope and emitted
    as a trailing USER message — or merged into the last event message if
    that is already user-role (preserving strict user/assistant alternation).

    Both the SYSTEM message and the trailing ``<context>`` USER message carry
    ``parts`` with per-block references so the journal publisher can
    content-address each block individually.
    """

    @property
    def format_type(self) -> FormatType:
        return FORMAT_XML

    def format(self, blocks: list[ResolvedBlock]) -> list[RenderedMessage]:
        system_blocks = [b for b in blocks if b.role == Role.SYSTEM]
        message_blocks = [b for b in blocks if b.role != Role.SYSTEM]

        immutable, volatile = _partition(system_blocks)

        messages: list[RenderedMessage] = []
        if immutable:
            content, parts = _concat_parts(immutable)
            messages.append(
                RenderedMessage(role=Role.SYSTEM, content=content, parts=parts)
            )

        # Event messages (wrap like XMLBlockFormatter does, except ToolCallEvents
        # still fan out into tool_call + tool_result messages).
        for block in message_blocks:
            messages.extend(
                _event_block_to_messages(
                    block,
                    wrap_content=_xml_message_content_shim,
                )
            )

        if volatile:
            volatile_rendered = [_xml_system_block(b) for b in volatile]
            suffix_inner = "\n".join(volatile_rendered)
            suffix = f"<context>\n{suffix_inner}\n</context>"
            # Build parts for the <context>...</context> envelope.
            envelope_parts: list[MessagePart] = [TextPart(text="<context>\n")]
            for i, (block, rendered) in enumerate(
                zip(volatile, volatile_rendered, strict=True)
            ):
                if i > 0:
                    envelope_parts.append(TextPart(text="\n"))
                envelope_parts.append(BlockPart(key=block.key, content=rendered))
            envelope_parts.append(TextPart(text="\n</context>"))

            if (
                messages
                and messages[-1].role == Role.USER
                and messages[-1].tool_call is None
                and messages[-1].tool_call_id is None
            ):
                # Merge with the trailing user event. Preserve the user event
                # as its own BlockPart (the event already has parts=[BlockPart(...)])
                # and append the <context> envelope after a "\n\n" separator.
                last = messages[-1]
                last_parts: list[MessagePart] = list(last.parts or [])
                if last.content:
                    merged_content = f"{last.content}\n\n{suffix}"
                    if last_parts:
                        last_parts.append(TextPart(text="\n\n"))
                    else:
                        last_parts.append(TextPart(text=last.content + "\n\n"))
                else:
                    merged_content = suffix
                last_parts.extend(envelope_parts)
                messages[-1] = last.model_copy(
                    update={"content": merged_content, "parts": last_parts}
                )
            else:
                messages.append(
                    RenderedMessage(role=Role.USER, content=suffix, parts=envelope_parts)
                )

        return messages


def _xml_message_content_shim(block: ResolvedBlock) -> str:
    """Small indirection so ToolCallEvent fan-out still works via the shared helper."""
    from context_blocks.formatter import _xml_message_content

    return _xml_message_content(block)
