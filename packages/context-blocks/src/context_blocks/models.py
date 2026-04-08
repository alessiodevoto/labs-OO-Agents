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
