# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Summarization agents for NeMo OO Agents.

This module provides agent-based event summarization following the NeMo OO Agents pattern.
Summarizers are proper agents that subscribe to a parent's event manager and use
LLM-generated code to produce summaries.

Example:
    from nemo_oo_agents import Agent
    from nemo_oo_agents.agents import TokenBudgetSummarizer

    class MyAgent(Agent, llm=my_llm):
        async def chat(self, message: str) -> str:
            '''Process a chat message.'''
            ...

    # Create agent and install summarizer
    agent = MyAgent()
    from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig
    TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=80_000))
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from agentdoc import hidden
from nemo_oo_agents.agent import Agent
from nemo_oo_agents.decorators import strategy
from nemo_oo_agents.strategies import PredictStrategy

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nemo_oo_agents.config.summarizer_config import MethodSummarizerConfig, TokenBudgetConfig
    from nemo_oo_agents.events import AfterTurn, EventBase
    from nemo_oo_agents.runtime.event_manager import EventManager


class SummarizationAgent(Agent):
    """Base class for summarization subagents.

    Summarizers subscribe to a parent agent's event manager and produce
    summaries when triggered. The actual summarization logic is LLM-generated.

    Subclasses define:
    - When to summarize (override _should_summarize)
    - What range to summarize (override _compute_range)

    The base class handles:
    - Event subscription to target_event_manager
    - Background task management
    - Applying summaries at safe boundaries (before_turn)
    - Clearing own events after each summarize() call (stateless)

    Configuration uses Annotated types for self-documentation:
        max_tokens: Annotated[int, "Description"] = 100_000
    """

    # Event manager whose events will be summarized. Wired automatically by the parent Agent.
    targetevent_manager: Annotated[EventManager | None, hidden] = None

    # Config must be set by subclasses (TokenBudgetSummarizer, MethodSummarizer set self.config
    # in __init__). The base class provides this sentinel to prevent AttributeError if a
    # subclass forgets to set it or if the base is instantiated directly.
    config: Annotated[Any, hidden] = None

    # Background task state
    _pending_task: Annotated[asyncio.Task | None, hidden]
    _pending_range: Annotated[tuple[str, str] | None, hidden]
    _pending_summary: Annotated[str | None, hidden]
    _unsub_before: Annotated[Callable[[], None] | None, hidden]
    _unsub_after: Annotated[Callable[[], None] | None, hidden]

    @classmethod
    def install(cls, agent: Agent, **kwargs) -> SummarizationAgent:
        """Install this summarizer on an agent.

        The summarizer is stored on the agent, so you don't need to keep
        a reference to it - its lifetime is tied to the agent's lifetime.

        Args:
            agent: Agent to attach to. The summarizer:
                   - Inherits LLM from agent (unless explicitly overridden)
                   - Attaches to agent's event manager automatically
            **kwargs: Passed to constructor (use config= on subclasses)

        Returns:
            The installed summarizer (usually not needed)

        Example:
            from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig
            agent = MyAgent(llm=my_llm)
            TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=80_000))
        """
        summarizer = cls(agent, **kwargs)

        # Store on agent to prevent GC - lifetime tied to agent
        if not hasattr(agent, "_summarizers"):
            agent._summarizers = []  # type: ignore[attr-defined]
        agent._summarizers.append(summarizer)  # type: ignore[attr-defined]

        return summarizer

    def __init__(self, agent: Agent, **kwargs):
        """Initialize summarization agent attached to a parent agent.

        Prefer using the `install()` class method instead of direct construction.

        Args:
            agent: Parent agent to attach to. The summarizer:
                   - Inherits LLM from agent (unless explicitly overridden)
                   - Attaches to agent's event manager automatically
            **kwargs: Passed through to Agent.__init__ (e.g. llm=)
        """
        # Inherit LLM from parent agent unless explicitly provided
        kwargs.setdefault("llm", agent._llm)
        self.target_event_manager = agent.event_manager

        # Extract annotated class attributes from kwargs (max_tokens, preserve_recent, etc.)
        for name in list(kwargs.keys()):
            if hasattr(self.__class__, name):
                setattr(self, name, kwargs.pop(name))

        super().__init__(**kwargs)

        # Background task state
        self._pending_task: asyncio.Task | None = None
        self._pending_range: tuple[str, str] | None = None
        self._pending_summary: str | None = None

        # Unsubscribe functions for cleanup
        self._unsub_before: Callable[[], None] | None = None
        self._unsub_after: Callable[[], None] | None = None

        # Install event subscriptions
        self._install()

    @hidden
    def _install(self) -> None:
        """Subscribe to target event manager.

        Called automatically if target_event_manager is set at creation,
        or by parent Agent after wiring target_event_manager.
        """
        if self.target_event_manager is None:
            raise ValueError("Cannot install: target_event_manager is None")

        self._unsub_before = self.target_event_manager.on("BeforeTurn", self._handle_before_turn)
        self._unsub_after = self.target_event_manager.on("AfterTurn", self._handle_after_turn)

    @hidden
    def _uninstall(self) -> None:
        """Unsubscribe from target event manager and cancel pending tasks."""
        if self._unsub_before:
            self._unsub_before()
            self._unsub_before = None
        if self._unsub_after:
            self._unsub_after()
            self._unsub_after = None
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            self._pending_task = None

    # -------------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------------

    @hidden
    def _handle_before_turn(self, event: EventBase) -> None:
        """Apply any pending summary before the next turn."""
        self._apply_pending_summary()

    @hidden
    def _handle_after_turn(self, event: EventBase) -> None:
        """Check if summarization needed and schedule it.

        Flow:
        1. If a previous summarization completed, apply it first
        2. If still over budget after applying, schedule another round
        3. Only skip if summarization is still actively running
        """
        from nemo_oo_agents.events import AfterTurn as _AfterTurn

        # Apply any completed summary first
        self._apply_pending_summary()

        # Don't start new summarization if one is STILL RUNNING
        if self._pending_task is not None and not self._pending_task.done():
            return

        # Note: if _pending_task was done, _apply_pending_summary() already cleared it.

        if not isinstance(event, _AfterTurn):
            return

        if self._should_summarize(event):
            range_result = self._compute_range(event)
            if range_result is not None:
                start_tag, end_tag = range_result
                self._schedule_summarization(start_tag, end_tag)

    # -------------------------------------------------------------------------
    # Override in subclasses
    # -------------------------------------------------------------------------

    @hidden
    def _should_summarize(self, event: AfterTurn) -> bool:
        """Decide if summarization is needed.

        Override in subclasses to implement policy logic.

        Args:
            event: The AfterTurn that triggered this check

        Returns:
            True if summarization should be scheduled
        """
        return False

    @hidden
    def _compute_range(self, event: AfterTurn) -> tuple[str, str] | None:
        """Compute the range of events to summarize.

        Override in subclasses to implement policy logic.

        Args:
            event: The AfterTurn that triggered this check

        Returns:
            (start_tag, end_tag) tuple, or None to skip summarization
        """
        return None

    # -------------------------------------------------------------------------
    # LLM-generated summarization
    # -------------------------------------------------------------------------

    @strategy(PredictStrategy())
    async def summarize(self, history_markdown: str, target_chars: int) -> str:
        """Summarize the `history_markdown` parameter into approximately {target_chars} characters.

        Structure your summary as:

        **Summary:**

        **Topics:** [2-4 bullet points]

        **Outcomes:** [Decisions, conclusions - preserve exact numbers/terms]

        **State:** [Current status, pending items]

        KEEP: Decisions, numbers, technical terms, conclusions
        COMPRESS: Discussions (themes only), Q&A (essence only)
        OMIT: Greetings, process details, resolved errors

        Target length: ~{target_chars} characters
        """
        # NOTE: Don't include {history_markdown} inline in the docstring - the
        # strategy already shows all parameters in the "Input parameters" section.
        # Including it here would duplicate the content and waste tokens.
        ...

    # -------------------------------------------------------------------------
    # Background task management
    # -------------------------------------------------------------------------

    @hidden
    def _schedule_summarization(self, start_tag: str, end_tag: str) -> None:
        """Schedule async summarization as a background task."""
        if self.target_event_manager is None:
            return

        self._pending_range = (start_tag, end_tag)

        # Render events to markdown for LLM consumption
        history_markdown = self._render_range_to_markdown(start_tag, end_tag)

        logger.debug(
            f"Scheduling summarization: {start_tag} -> {end_tag} ({len(history_markdown)} chars)"
        )

        # Schedule the async summarization
        self._pending_task = asyncio.create_task(
            self._run_summarization(history_markdown, start_tag, end_tag)
        )

    @hidden
    async def _run_summarization(self, history_markdown: str, start_tag: str, end_tag: str) -> None:
        """Run summarization and store result for later application."""
        try:
            summary = await self.summarize(history_markdown, self.config.target_chars)
            self._pending_summary = summary
            logger.debug(
                f"Summarization complete: {start_tag} -> {end_tag} (summary: {len(summary)} chars)"
            )
        except Exception as e:
            # Log error but don't crash - summarization is best-effort
            logger.warning(f"Summarization failed: {e}")
            self._pending_summary = None
        finally:
            # Clear our own events to stay stateless
            self.event_manager.clear()

    @hidden
    def _apply_pending_summary(self) -> None:
        """Apply completed summary to target event manager."""
        if self._pending_task is None:
            return

        if not self._pending_task.done():
            return

        # Get the result (summary is stored in _pending_summary by _run_summarization)
        if self._pending_summary is not None and self._pending_range is not None:
            start_tag, end_tag = self._pending_range
            try:
                assert self.target_event_manager is not None
                self.target_event_manager.collapse(start_tag, end_tag, self._pending_summary)
                logger.debug(f"Applied summary: {start_tag} -> {end_tag}")
            except Exception as e:
                logger.warning(f"Failed to apply summary: {e}")

        # Clear pending state
        self._pending_task = None
        self._pending_range = None
        self._pending_summary = None

    # -------------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------------

    @hidden
    def _get_events_in_range(self, start_tag: str, end_tag: str) -> list[tuple[str, EventBase]]:
        """Get events between start_tag and end_tag (inclusive).

        Use this in generated code to programmatically investigate raw events.

        Args:
            start_tag: First event tag in the range
            end_tag: Last event tag in the range

        Returns:
            List of (tag, event) tuples
        """
        if self.target_event_manager is None:
            return []

        result = []
        # keys() returns tags in chronological (insertion) order
        tags = self.target_event_manager.keys()

        in_range = False
        for tag in tags:
            if tag == start_tag:
                in_range = True
            if in_range:
                event = self.target_event_manager[tag]
                result.append((tag, event))
            if tag == end_tag:
                break

        return result

    @hidden
    def _render_range_to_markdown(self, start_tag: str, end_tag: str) -> str:
        """Render events in range to markdown for LLM consumption.

        Uses context-blocks MarkdownBlockFormatter for consistent rendering
        with proper truncation and pformat handling.

        Args:
            start_tag: First event tag in the range
            end_tag: Last event tag in the range

        Returns:
            Markdown-formatted events section
        """
        if self.target_event_manager is None:
            return ""

        events = self._get_events_in_range(start_tag, end_tag)

        if not events:
            return ""

        from context_blocks import BlockMetadata, ResolvedBlock, Role, format_message_content
        from context_blocks.utils import truncating_pformat

        parts = []
        for tag, event in events:
            event_role = getattr(event, "_role", Role.USER)
            body = truncating_pformat(event, max_chars=self._truncation.max_block_chars)
            block = ResolvedBlock(
                key=f"event_{tag}",
                content=body,
                role=event_role,
                metadata=BlockMetadata(expr=f'self.events["{tag}"]', tag=str(tag)),
            )
            parts.append(format_message_content(block, "markdown"))

        return "\n\n".join(parts)

    @hidden
    def _estimate_tokens(self) -> int:
        """Estimate total tokens in target event manager."""
        if self.target_event_manager is None:
            return 0

        # Render all active events to markdown for token estimation
        tags = self.target_event_manager.keys()
        if not tags:
            return 0

        # Render using the same method we use for summarization
        first_tag = tags[0]
        last_tag = tags[-1]
        rendered = self._render_range_to_markdown(first_tag, last_tag)

        # Use LLM's count_tokens if available
        if self._llm and hasattr(self._llm, "count_tokens"):
            return self._llm.count_tokens(rendered)

        # Fallback: chars / 4 heuristic
        return len(rendered) // 4


# =============================================================================
# Helper Functions
# =============================================================================


def context_budget(llm, percent: float = 0.8, fallback: int = 100_000) -> int:
    """Calculate token budget as percentage of LLM context limit.

    Args:
        llm: LLM instance with context_limit attribute
        percent: Fraction of context to use (0.0-1.0), default 0.8 (80%)
        fallback: Value if LLM doesn't expose context_limit

    Returns:
        Token budget as integer

    Example:
        TokenBudgetSummarizer.install(agent, max_tokens=context_budget(my_llm, 0.8))
    """
    context_limit = getattr(llm, "context_limit", None)
    if context_limit is not None:
        return int(context_limit * percent)
    return fallback


# =============================================================================
# Example Summarizers (Good Defaults)
# =============================================================================


class TokenBudgetSummarizer(SummarizationAgent):
    """Summarize when event count exceeds token budget.

    Trigger: Event tokens > config.max_tokens
    Action: Summarize oldest events, preserve N most recent

    Example:
        from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig
        # Absolute limit
        TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=80_000))

        # Percentage of LLM context
        TokenBudgetSummarizer.install(agent, config=TokenBudgetConfig(max_tokens=context_budget(my_llm, 0.8)))
    """

    @classmethod
    def install(
        cls, agent: Agent, *, config: TokenBudgetConfig | None = None, **kwargs
    ) -> SummarizationAgent:
        """Install with a TokenBudgetConfig.

        Args:
            agent: Agent to attach to.
            config: TokenBudgetConfig instance. Use TokenBudgetConfig(field=value) to override.
            **kwargs: Only 'llm' is allowed; all other flat kwargs raise TypeError.
        """
        unknown = set(kwargs) - {"llm"}
        if unknown:
            raise TypeError(
                f"TokenBudgetSummarizer.install() got unexpected keyword arguments: "
                f"{sorted(unknown)}. Use config=TokenBudgetConfig(...) instead."
            )
        return super().install(agent, config=config, **kwargs)

    def __init__(self, agent: Agent, **kwargs):
        from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig as _TBC

        config = kwargs.pop("config", None)
        self.config = config or _TBC()
        super().__init__(agent, **kwargs)

    @hidden
    def _should_summarize(self, event: AfterTurn) -> bool:
        """Trigger when over token budget."""
        return self._estimate_tokens() > self.config.max_tokens

    @hidden
    def _compute_range(self, event: AfterTurn) -> tuple[str, str] | None:
        """Summarize oldest events, preserving recent ones."""
        if self.target_event_manager is None:
            return None

        tags = self.target_event_manager.keys()
        if len(tags) <= self.config.preserve_recent:
            return None

        # Summarize from oldest to (len - preserve_recent - 1)
        start_tag = tags[0]
        end_tag = tags[-(self.config.preserve_recent + 1)]
        return (start_tag, end_tag)


class MethodSummarizer(SummarizationAgent):
    """Summarize after each method call completes.

    Trigger: event.is_final == True (method completed)
    Action: Summarize all events from that method's generation_id

    Example:
        from nemo_oo_agents.config.summarizer_config import MethodSummarizerConfig
        agent = MyAgent(llm=my_llm)
        MethodSummarizer.install(agent, config=MethodSummarizerConfig(min_events=5))
    """

    @classmethod
    def install(
        cls, agent: Agent, *, config: MethodSummarizerConfig | None = None, **kwargs
    ) -> SummarizationAgent:
        """Install with a MethodSummarizerConfig.

        Args:
            agent: Agent to attach to.
            config: MethodSummarizerConfig instance.
            **kwargs: Only 'llm' is allowed; all other flat kwargs raise TypeError.
        """
        unknown = set(kwargs) - {"llm"}
        if unknown:
            raise TypeError(
                f"MethodSummarizer.install() got unexpected keyword arguments: "
                f"{sorted(unknown)}. Use config=MethodSummarizerConfig(...) instead."
            )
        return super().install(agent, config=config, **kwargs)

    def __init__(self, agent: Agent, **kwargs):
        from nemo_oo_agents.config.summarizer_config import MethodSummarizerConfig as _MSC

        config = kwargs.pop("config", None)
        self.config = config or _MSC()
        super().__init__(agent, **kwargs)

    @hidden
    def _should_summarize(self, event: AfterTurn) -> bool:
        """Trigger on method completion."""
        if not event.is_final:
            return False

        # Optionally exclude root calls
        if self.config.exclude_root and self._is_root_call(event):
            return False

        return True

    @hidden
    def _compute_range(self, event: AfterTurn) -> tuple[str, str] | None:
        """Summarize all events from this method invocation (including children).

        Uses call_id from event metadata to find the range. Child method calls
        (nested call_ids) are included because they fall chronologically between
        the first and last event with the parent call_id.
        """
        if self.target_event_manager is None:
            return None

        call_id = event.metadata.get("call_id")
        if call_id is None:
            return None

        # Find first and last tags with this call_id.
        # Events from child calls (different call_ids) are interleaved
        # chronologically, so the range naturally includes them.
        first_tag: str | None = None
        last_tag: str | None = None
        match_count = 0
        for tag in self.target_event_manager.keys():
            evt = self.target_event_manager[tag]
            if evt.metadata.get("call_id") == call_id:
                if first_tag is None:
                    first_tag = tag
                last_tag = tag
                match_count += 1

        if match_count < self.config.min_events:
            return None

        return (first_tag, last_tag)  # type: ignore[return-value]

    @hidden
    def _is_root_call(self, event: AfterTurn) -> bool:
        """Check if this is a root (top-level) method call."""
        # Root calls typically don't have a parent generation context
        # This is a heuristic - could be refined based on actual context
        return event.turn_number == 1 and event.is_final
