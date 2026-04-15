# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Context builder: the pipeline that gathers, resolves, and orders context blocks.

Extracted from actor.py so _prepare_context() is thin.

Pipeline phases (must run in order — later phases override earlier ones):
    1. Framework blocks (system_prompt, self-doc, context_api, events_api)
    2. Persistent blocks from self.context (ContextApi)
    3. Strategy overrides (strategy.get_block_overrides())
    4. Decorator context (@strategy(ScopedContext(context={...})))
    5. Scoped context (with ScopedContext(context={...}))
    6. Events -> ResolvedBlocks with roles
       Event filtering priority: runtime > scoped > decorator > agent > all events

All phase functions are pure — they return new lists, never mutate inputs.
The only side effect (updating ContextApi's resolved cache) is performed
by the caller in _prepare_context(), not by build_context() itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, NamedTuple

from context_blocks import BlockMetadata, DynamicContext, ResolvedBlock, Role
from context_blocks.utils import truncating_pformat

if TYPE_CHECKING:
    from nemo_oo_agents.agent import FrameworkBlock
    from nemo_oo_agents.runtime.context_manager import ContextManager
    from nemo_oo_agents.runtime.event_query import EventQuery
    from nemo_oo_agents.strategies.base import GenerationStrategy

logger = logging.getLogger(__name__)

# Type alias for the async resolve function
ResolveFunc = Callable[[str, "str | DynamicContext"], Coroutine[Any, Any, "str | None"]]


# ---------------------------------------------------------------------------
# Pure list helpers — always return new lists, never mutate inputs
# ---------------------------------------------------------------------------


async def _apply_overrides(
    blocks: list[ResolvedBlock],
    overrides: dict[str, str | DynamicContext | None],
    resolve_fn: ResolveFunc,
    static_expr: Callable[[str], str],
) -> list[ResolvedBlock]:
    """Apply a dict of overrides (replace/append/remove) to blocks.

    Shared logic for strategy overrides, decorator context, and scoped blocks.
    Uses a single pass with an index for O(n + k) instead of O(n * k).

    Args:
        blocks: Current block list (not mutated).
        overrides: Dict of key -> value. None removes, str/DynamicContext replaces or appends.
        resolve_fn: Async function to resolve DynamicContext values.
        static_expr: Function(key) -> metadata expr string for static values.
    """
    if not overrides:
        return blocks

    # Build index: key -> position in blocks list (first occurrence)
    index: dict[str, int] = {}
    for i, b in enumerate(blocks):
        if b.key not in index:
            index[b.key] = i

    # Resolve all overrides and build a replacement map + append list
    replacements: dict[int, ResolvedBlock | None] = {}  # position -> new block (None = remove)
    appends: list[ResolvedBlock] = []

    for key, value in overrides.items():
        if value is None:
            if key in index:
                replacements[index[key]] = None
            continue

        content = await resolve_fn(key, value)
        if content is None:
            content = "None"

        if isinstance(value, DynamicContext):
            meta = BlockMetadata(expr=value.expr)
        else:
            meta = BlockMetadata(expr=static_expr(key))

        new_block = ResolvedBlock(key=key, content=content, role=Role.SYSTEM, metadata=meta)
        if key in index:
            replacements[index[key]] = new_block
        else:
            appends.append(new_block)

    # Single pass: rebuild list applying replacements and filtering removals
    result = []
    for i, block in enumerate(blocks):
        if i in replacements:
            replacement = replacements[i]
            if replacement is not None:
                result.append(replacement)
            # else: removed (skip)
        else:
            result.append(block)

    result.extend(appends)
    return result


# ---------------------------------------------------------------------------
# Block reordering — applied after all content phases, before events
# ---------------------------------------------------------------------------


def _reorder_blocks(
    blocks: list[ResolvedBlock],
    strategy: GenerationStrategy | None,
) -> list[ResolvedBlock]:
    """Reorder system blocks according to strategy.get_block_order().

    Listed keys appear first (in given order), unlisted system blocks follow
    in their original relative order. Returns a new list.
    """
    if strategy is None:
        return blocks

    order = strategy.get_block_order()
    if order is None:
        return blocks

    key_rank = {key: i for i, key in enumerate(order)}

    # Partition into ordered (has a rank) and remainder (no rank)
    ordered: list[tuple[int, ResolvedBlock]] = []
    remainder: list[ResolvedBlock] = []
    for block in blocks:
        if block.key in key_rank:
            ordered.append((key_rank[block.key], block))
        else:
            remainder.append(block)

    ordered.sort(key=lambda pair: pair[0])
    return [block for _, block in ordered] + remainder


# ---------------------------------------------------------------------------
# Result type for build_context — separates blocks from resolved cache
# ---------------------------------------------------------------------------


class BuildResult(NamedTuple):
    """Result of build_context(): resolved blocks + cache for ContextApi."""

    blocks: list[ResolvedBlock]
    resolved_cache: dict[str, Any]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def build_context(
    *,
    framework_blocks: dict[str, FrameworkBlock],
    context_manager: ContextManager,
    event_manager: Any,
    strategy: GenerationStrategy | None,
    resolve_fn: ResolveFunc,
    decorator_context: dict[str, Any] | None = None,
    scoped_context: dict[str, Any] | None = None,
    runtime_event_query: EventQuery | None = None,
    decorator_event_query: EventQuery | None = None,
    scoped_event_query: EventQuery | None = None,
    agent_event_query: EventQuery | None = None,
    current_call_id: str | None = None,
    pre_format_chars: int | None = None,
) -> BuildResult:
    """Build the complete context block list from all sources.

    This is a pure pipeline — it does NOT mutate agent or context_manager state.
    The caller is responsible for applying resolved_cache via
    ``context_manager._update_resolved(result.resolved_cache)``.

    Args:
        framework_blocks: Framework block definitions (from Agent._framework_blocks).
        context_manager: The agent's ContextManager (read-only here).
        event_manager: The agent's EventManager (for events phase).
        strategy: Current generation strategy (or None).
        resolve_fn: Async function(key, value) -> str that resolves DynamicContext values.
        decorator_context: Merged @strategy(context={...}) overrides from the
            current method and its parents. Passed explicitly by the actor
            instead of being read from a context variable.
        scoped_context: Scoped block overrides from with ScopedContext(...).
            Passed explicitly by the actor instead of reading from contextvars.
        runtime_event_query: EventQuery from event_manager.set_event_query().
        decorator_event_query: EventQuery from @strategy(ScopedContext(events=...)).
        scoped_event_query: EventQuery from with ScopedContext(events=...).
        agent_event_query: Default EventQuery from agent-level configuration.
        current_call_id: Current call ID for resolving EventQuery(call_id="current").

    Returns:
        BuildResult with blocks and resolved_cache.
    """
    blocks: list[ResolvedBlock] = []

    # --- Framework blocks ---
    blocks = await _phase_framework_blocks(blocks, framework_blocks, resolve_fn)

    # --- Persistent blocks from self.context ---
    blocks, resolved_cache = await _phase_persistent_blocks(
        blocks, context_manager, resolve_fn, pre_format_chars=pre_format_chars
    )

    # --- Strategy block overrides ---
    blocks = await _phase_strategy_overrides(blocks, strategy, resolve_fn)

    # --- @strategy(context=...) decorator overrides ---
    blocks = await _phase_decorator_context(blocks, decorator_context, resolve_fn)

    # --- Scoped context blocks ---
    blocks = await _phase_scoped_blocks(blocks, scoped_context, resolve_fn)

    # --- Strategy block reordering (between content phases and events) ---
    blocks = _reorder_blocks(blocks, strategy)

    # --- Events (with optional filtering via EventQuery) ---
    blocks = _phase_events(
        blocks,
        event_manager,
        runtime_event_query=runtime_event_query,
        scoped_event_query=scoped_event_query,
        decorator_event_query=decorator_event_query,
        agent_event_query=agent_event_query,
        current_call_id=current_call_id,
    )

    return BuildResult(blocks=blocks, resolved_cache=resolved_cache)


async def _phase_framework_blocks(
    blocks: list[ResolvedBlock],
    framework_blocks: dict[str, FrameworkBlock],
    resolve_fn: ResolveFunc,
) -> list[ResolvedBlock]:
    """Add framework blocks (system_prompt, self-doc, etc.)."""
    for key, fb in framework_blocks.items():
        value = fb.value
        content = await resolve_fn(key, value)
        if content is None:
            continue

        if isinstance(value, DynamicContext):
            meta = BlockMetadata(expr=value.expr)
        else:
            meta = BlockMetadata(expr=f'self.context["{key}"]')

        blocks = [*blocks, ResolvedBlock(key=key, content=content, role=Role.SYSTEM, metadata=meta)]

    return blocks


async def _phase_persistent_blocks(
    blocks: list[ResolvedBlock],
    context_manager: ContextManager,
    resolve_fn: ResolveFunc,
    pre_format_chars: int | None = None,
) -> tuple[list[ResolvedBlock], dict[str, Any]]:
    """Add persistent blocks from ContextApi (user-set via self.context).

    These blocks are tagged as ``user_block=True`` in metadata so that
    truncation can prioritize dropping them before framework/strategy blocks.

    Returns:
        Tuple of (updated blocks, resolved_cache dict).
        The caller should pass resolved_cache to context_manager._update_resolved().
    """
    resolved_cache: dict[str, Any] = {}

    for key, value in context_manager._raw_items():
        if isinstance(value, DynamicContext):
            # DynamicContext path: resolve via async eval
            # resolve_fn returns pre-formatted string (already pprinted if non-string)
            resolved = await resolve_fn(key, value)
            content = resolved if resolved is not None else "None"
            meta = BlockMetadata(expr=value.expr, user_block=True)
            # Cache the resolved value for __getitem__ access
            resolved_cache[key] = resolved
        else:
            # Static path: use value directly, cap all values to prevent OOM
            if value is None:
                content = "None"
            else:
                kwargs = {} if pre_format_chars is None else {"max_chars": pre_format_chars}
                content = truncating_pformat(value, **kwargs)
            meta = BlockMetadata(expr=f'self.context["{key}"]', user_block=True)

        blocks = [
            *blocks,
            ResolvedBlock(key=key, content=content, role=Role.SYSTEM, metadata=meta),
        ]

    return blocks, resolved_cache


async def _phase_strategy_overrides(
    blocks: list[ResolvedBlock],
    strategy: GenerationStrategy | None,
    resolve_fn: ResolveFunc,
) -> list[ResolvedBlock]:
    """Apply strategy.get_block_overrides()."""
    if not strategy or not hasattr(strategy, "get_block_overrides"):
        return blocks

    return await _apply_overrides(
        blocks,
        strategy.get_block_overrides(),
        resolve_fn,
        static_expr=lambda key: f"strategy.{key}",
    )


async def _phase_decorator_context(
    blocks: list[ResolvedBlock],
    decorator_context: dict[str, Any] | None,
    resolve_fn: ResolveFunc,
) -> list[ResolvedBlock]:
    """Apply @strategy(context=...) decorator overrides.

    The decorator_context dict is the merged context from the current method's
    @strategy(context={...}) and its parent's inherited context. Passed
    explicitly by the actor.
    """
    if not decorator_context:
        return blocks

    return await _apply_overrides(
        blocks,
        decorator_context,
        resolve_fn,
        static_expr=lambda key: f'@strategy.context["{key}"]',
    )


async def _phase_scoped_blocks(
    blocks: list[ResolvedBlock],
    scoped_context: dict[str, Any] | None,
    resolve_fn: ResolveFunc,
) -> list[ResolvedBlock]:
    """Apply scoped block overrides.

    Scoped context is passed explicitly by the caller instead of reading
    from _scoped_blocks_var, keeping this function pure.
    """
    if not scoped_context:
        return blocks

    return await _apply_overrides(
        blocks,
        scoped_context,
        resolve_fn,
        static_expr=lambda key: f'self.context["{key}"]',
    )


def _phase_events(
    blocks: list[ResolvedBlock],
    event_manager: Any,
    *,
    runtime_event_query: EventQuery | None = None,
    scoped_event_query: EventQuery | None = None,
    decorator_event_query: EventQuery | None = None,
    agent_event_query: EventQuery | None = None,
    current_call_id: str | None = None,
) -> list[ResolvedBlock]:
    """Convert events to ResolvedBlocks with appropriate roles.

    Events are filtered using EventQuery with 4-level priority:
    1. Runtime query (set via event_manager.set_event_query()) - highest priority
    2. Scoped query (with ScopedContext(events=...)) - high priority
    3. Decorator query (@strategy(ScopedContext(events=...))) - medium priority
    4. Agent query (agent-level default) - low priority
    5. No filter (show all events) - default

    The original event is carried on ``block.event`` so that provider
    formatters can read structured data (e.g., ToolCallEvent fields)
    directly without intermediate types.

    Args:
        blocks: Current block list (not mutated).
        event_manager: EventManager for accessing events.
        runtime_event_query: EventQuery from event_manager.set_event_query().
        scoped_event_query: EventQuery from with ScopedContext(events=...).
        decorator_event_query: EventQuery from @strategy(ScopedContext(events=...)).
        agent_event_query: EventQuery from agent-level configuration.
        current_call_id: Current call ID for resolving EventQuery(call_id="current").
    """
    # Determine active query (runtime > scoped > decorator > agent > none)
    active_query = (
        runtime_event_query or scoped_event_query or decorator_event_query or agent_event_query
    )

    # Use only active (non-archived) events in their display order.
    # values() uses active_tags() — excludes events archived by collapse().
    if active_query:
        events = active_query.apply(event_manager.values(), current_call_id=current_call_id)
    else:
        events = event_manager.values()

    new_blocks: list[ResolvedBlock] = []

    for event in events:
        tag = event.tag if event.tag is not None else event.id
        event_role = getattr(event, "_role", Role.USER)
        meta = BlockMetadata(expr=f'self.events["{tag}"]', tag=tag)

        # All events carry their raw object on block.event with content="".
        # ToolCallEvents are handled by ProviderFormatter; other events are
        # serialized at render time via block_formatter.format_event().
        new_blocks.append(
            ResolvedBlock(
                key=f"event_{tag}",
                content="",
                role=event_role,
                metadata=meta,
                event=event,
            )
        )

    return [*blocks, *new_blocks]


# Phase 7 and 8 (decorator events and scoped events) have been removed.
# Events are now filtered directly in _phase_events() using EventQuery.
