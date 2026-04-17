# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Core types for context blocks.

DynamicContext: Marks a context block for dynamic evaluation each turn.
ResolvedBlock: A fully-resolved block ready for rendering.
BlockMetadata: Typed metadata for resolved blocks.
Role: Re-exported from roles.py for backward compatibility.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# Import EventBase here (not forward ref) — possible because events.py imports
# Role from roles.py, breaking the circular dependency.
from context_blocks.events import EventBase
from context_blocks.exceptions import BlockSyntaxError
from context_blocks.roles import Role  # noqa: F401 — re-exported for backward compat


class DynamicContext(BaseModel):
    """Marks a context block for dynamic evaluation each turn.

    Wraps a Python expression string that will be evaluated by the runtime
    at each LLM turn. The expression is validated at creation time.

    Usage:
        self.context.set_dynamic("status", "self.format_status()")
        self.context.set_dynamic("progress", "self.todo.show_active()")

    The expression must be valid Python (compilable as an eval expression).
    """

    model_config = ConfigDict(frozen=True)

    expr: Annotated[str, Field(description="Python expression to evaluate each turn")]

    def __init__(self, expr: str, **kwargs: Any):
        """Create a DynamicContext block marker.

        Args:
            expr: Python expression to evaluate each turn.

        Raises:
            BlockSyntaxError: If expr is not valid Python syntax.
        """
        try:
            compile(expr, "<block_expr>", "eval")
        except SyntaxError as e:
            raise BlockSyntaxError(key="<dynamic>", expr=expr, original_error=e) from e
        super().__init__(expr=expr, **kwargs)

    def __repr__(self) -> str:
        return f"DynamicContext({self.expr!r})"


class BlockMetadata(BaseModel):
    """Typed metadata for resolved blocks.

    Replaces the untyped dict[str, Any] with well-defined fields.
    Used by formatters and provider formatters to render blocks correctly.
    """

    model_config = ConfigDict(frozen=True)

    expr: str | None = Field(default=None, description="Python expression for accessing this block")
    tag: str | None = Field(default=None, description="Event position tag")
    truncated: bool = Field(default=False, description="Whether content was truncated")
    user_block: bool = Field(
        default=False,
        description="Whether this is a user-set block (from self.context). "
        "User blocks are dropped first during context truncation.",
    )


class ResolvedBlock(BaseModel):
    """A fully-resolved block ready for rendering.

    All content has been evaluated — no expressions, no Dynamic markers.
    The renderer receives these and formats them without any evaluation.

    For event blocks, the original event is carried through on the ``event``
    field. Provider formatters use this for tool call events (which need
    structured fields like function name, arguments, tool_call_id) and to
    read the event's ``_role``.
    """

    model_config = ConfigDict(frozen=True)

    key: Annotated[str, Field(description="Unique identifier for the block")]
    content: Annotated[str, Field(description="Pre-resolved string content")]
    role: Role = Field(
        default=Role.SYSTEM,
        description="Message role (SYSTEM for system prompt, USER/ASSISTANT/TOOL for messages)",
    )
    metadata: BlockMetadata = Field(default_factory=BlockMetadata, description="Block metadata for rendering")
    event: EventBase | None = Field(default=None, description="Original event, if this block represents one")


class ContextWindowStats(BaseModel):
    """Context window utilization snapshot from render_context().

    Token counts use ``count_tokens`` when available, otherwise fall back
    to character length as an approximation.

    Sizes are measured on the raw block content *after* truncation but
    *before* provider formatting. ToolCallEvent blocks with ``content=""``
    are counted as zero — the real token cost of their structured fields
    is added by the provider formatter and not reflected here.
    """

    model_config = ConfigDict(frozen=True)

    context_blocks_tokens: Annotated[int, Field(description="Total tokens in system blocks (post-truncation)")]
    context_blocks_count: Annotated[int, Field(description="Number of system blocks (post-truncation)")]
    events_tokens: Annotated[int, Field(description="Total tokens in event blocks (post-truncation)")]
    events_count: Annotated[int, Field(description="Number of event blocks (post-truncation)")]
    total_tokens: Annotated[int, Field(description="context_blocks_tokens + events_tokens")]
    max_context_tokens: Annotated[int | None, Field(description="Configured limit for context blocks")] = None
    max_event_tokens: Annotated[int | None, Field(description="Configured limit for events")] = None
    model_context_window: Annotated[int | None, Field(description="Model's total context window size")] = None
    context_blocks_dropped: Annotated[int, Field(description="System blocks dropped during truncation")] = 0
    events_dropped: Annotated[int, Field(description="Events dropped during truncation")] = 0

    @property
    def context_utilization(self) -> float | None:
        """Fraction of context block budget used, or None if no limit.

        Usually 0.0-1.0, but can exceed 1.0 when the truncation notice
        block (appended after dropping blocks) pushes the total over budget.
        """
        if not self.max_context_tokens:
            return None
        return self.context_blocks_tokens / self.max_context_tokens

    @property
    def event_utilization(self) -> float | None:
        """Fraction of event budget used, or None if no limit."""
        if not self.max_event_tokens:
            return None
        return self.events_tokens / self.max_event_tokens

    def format(self) -> str:
        """Human-readable context window summary, suitable for a context block.

        Example output (with per-category limits)::

            Context usage: 12,450 / 52,000 tokens (23.9%)
              Context blocks: 8,200 / 32,000 tokens (25.6%) — 6 blocks
              Events:         4,250 / 20,000 tokens (21.2%) — 18 events

        Example output (no per-category limits, model window known)::

            Context usage: 12,450 / 200,000 tokens (6.2%)
              Context blocks: 8,200 tokens — 6 blocks
              Events:         4,250 tokens — 18 events
        """
        lines: list[str] = []

        # --- Header line ---
        max_total = None
        if self.max_context_tokens is not None and self.max_event_tokens is not None:
            max_total = self.max_context_tokens + self.max_event_tokens
        elif self.model_context_window:
            max_total = self.model_context_window

        if max_total:
            pct = self.total_tokens / max_total * 100
            lines.append(f"Context usage: {self.total_tokens:,} / {max_total:,} tokens ({pct:.1f}%)")
        else:
            lines.append(f"Context usage: {self.total_tokens:,} tokens")

        # --- Context blocks line ---
        cb_parts = []
        if self.max_context_tokens:
            pct = self.context_blocks_tokens / self.max_context_tokens * 100
            cb_parts.append(f"{self.context_blocks_tokens:,} / {self.max_context_tokens:,} tokens ({pct:.1f}%)")
        else:
            cb_parts.append(f"{self.context_blocks_tokens:,} tokens")
        cb_parts.append(f"{self.context_blocks_count} blocks")
        if self.context_blocks_dropped:
            cb_parts.append(f"{self.context_blocks_dropped} dropped")
        lines.append(f"  Context blocks: {' — '.join(cb_parts)}")

        # --- Events line ---
        ev_parts = []
        if self.max_event_tokens:
            pct = self.events_tokens / self.max_event_tokens * 100
            ev_parts.append(f"{self.events_tokens:,} / {self.max_event_tokens:,} tokens ({pct:.1f}%)")
        else:
            ev_parts.append(f"{self.events_tokens:,} tokens")
        ev_parts.append(f"{self.events_count} events")
        if self.events_dropped:
            ev_parts.append(f"{self.events_dropped} dropped")
        lines.append(f"  Events:         {' — '.join(ev_parts)}")

        # --- Warning ---
        ctx_hot = self.context_utilization is not None and self.context_utilization > 0.8
        evt_hot = self.event_utilization is not None and self.event_utilization > 0.8
        if self.context_blocks_dropped or self.events_dropped or ctx_hot or evt_hot:
            lines.append(
                "Context is nearly full. Use self.context (ContextApi) to summarize "
                "or remove blocks, and self.events (EventsApi) to summarize or "
                "manage event history."
            )

        return "\n".join(lines)
