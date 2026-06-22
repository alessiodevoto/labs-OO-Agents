# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ContextWindowStats computed by render_context()."""

from nemo_oo_agents.context_blocks.formatter import OpenAIProviderFormatter, XMLBlockFormatter
from nemo_oo_agents.context_blocks.models import (
    BlockMetadata,
    ContextWindowStats,
    ResolvedBlock,
    Role,
)
from nemo_oo_agents.context_blocks.renderer import RenderResult, render_context


class TestContextWindowStatsBasic:
    """Basic stats computation tests."""

    def test_stats_returned_as_render_result(self):
        """render_context() returns a RenderResult with output and stats."""
        result = render_context(
            [],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        )
        assert isinstance(result, RenderResult)
        assert isinstance(result.stats, ContextWindowStats)

    def test_context_blocks_counted(self):
        """System blocks counted in context_blocks_count."""
        blocks = [
            ResolvedBlock(key="a", content="AAA"),
            ResolvedBlock(key="b", content="BBB"),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.context_blocks_count == 2
        assert stats.context_blocks_tokens == 6  # len("AAA") + len("BBB")

    def test_events_counted(self):
        """Message blocks counted in events_count."""
        blocks = [
            ResolvedBlock(key="e1", content="hello", role=Role.USER),
            ResolvedBlock(key="e2", content="world", role=Role.ASSISTANT),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.events_count == 2
        assert stats.events_tokens == 10  # len("hello") + len("world")

    def test_total_tokens_is_sum(self):
        """total_tokens == context_blocks_tokens + events_tokens."""
        blocks = [
            ResolvedBlock(key="sys", content="system"),
            ResolvedBlock(key="msg", content="user msg", role=Role.USER),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.total_tokens == stats.context_blocks_tokens + stats.events_tokens
        assert stats.total_tokens == len("system") + len("user msg")

    def test_empty_blocks(self):
        """Stats for empty input are all zero."""
        stats = render_context(
            [],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.context_blocks_count == 0
        assert stats.context_blocks_tokens == 0
        assert stats.events_count == 0
        assert stats.events_tokens == 0
        assert stats.total_tokens == 0


class TestContextWindowStatsUtilization:
    """Tests for utilization properties."""

    def test_context_utilization_when_limit_set(self):
        """context_utilization computed as tokens/limit."""
        blocks = [ResolvedBlock(key="sys", content="x" * 500)]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        ).stats
        assert stats.max_context_tokens == 1000
        assert stats.context_utilization == 500 / 1000

    def test_context_utilization_none_when_no_limit(self):
        """context_utilization is None when no limit configured."""
        blocks = [ResolvedBlock(key="sys", content="hello")]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.max_context_tokens is None
        assert stats.context_utilization is None

    def test_event_utilization_none_when_no_event_limit(self):
        """event_utilization is None when no event limit configured."""
        blocks = [ResolvedBlock(key="msg", content="x" * 300, role=Role.USER)]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.max_event_tokens is None
        assert stats.event_utilization is None

    def test_event_utilization_none_when_no_limit(self):
        """event_utilization is None when no limit configured."""
        blocks = [ResolvedBlock(key="msg", content="hello", role=Role.USER)]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.max_event_tokens is None
        assert stats.event_utilization is None


class TestContextWindowStatsTruncation:
    """Tests for dropped block/event tracking."""

    def test_context_blocks_dropped_on_truncation(self):
        """context_blocks_dropped tracks how many system blocks were dropped."""
        blocks = [
            ResolvedBlock(key="small", content="#" * 100),
            ResolvedBlock(key="large", content="$" * 5000),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        ).stats
        assert stats.context_blocks_dropped == 1
        # "small" (100) survives + truncation_notice; "large" (5000) was dropped
        assert stats.context_blocks_tokens < 1000

    def test_context_blocks_dropped_multiple(self):
        """Multiple blocks dropped are counted."""
        blocks = [
            ResolvedBlock(key="tiny", content="#" * 50),
            ResolvedBlock(key="medium", content="$" * 3000),
            ResolvedBlock(key="huge", content="@" * 8000),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=500,
            count_tokens=len,
        ).stats
        assert stats.context_blocks_dropped == 2

    def test_no_drops_when_under_budget(self):
        """No drops when everything fits."""
        blocks = [
            ResolvedBlock(key="a", content="hello"),
            ResolvedBlock(key="b", content="world"),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=10000,
            count_tokens=len,
        ).stats
        assert stats.context_blocks_dropped == 0
        assert stats.events_dropped == 0

    def test_stats_reflect_post_truncation(self):
        """Token counts reflect post-truncation state."""
        blocks = [
            ResolvedBlock(key="small", content="x" * 100),
            ResolvedBlock(key="big", content="y" * 10000),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=500,
            count_tokens=len,
        ).stats
        # "big" was dropped, only "small" (100) + truncation_notice (~200) survive
        assert stats.context_blocks_dropped == 1
        assert stats.context_blocks_tokens < 500

    def test_all_context_blocks_dropped(self):
        """When all original blocks are dropped, only the notice remains."""
        blocks = [
            ResolvedBlock(key="a", content="x" * 5000),
            ResolvedBlock(key="b", content="y" * 5000),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=1,
            count_tokens=len,
        ).stats
        assert stats.context_blocks_dropped == 2
        # Blocks are retained in-place and labeled EVICTED
        assert stats.context_blocks_count == 2

    def test_context_blocks_dropped_with_user_blocks(self):
        """User blocks (from self.context) are dropped first."""
        blocks = [
            ResolvedBlock(key="framework", content="x" * 100),
            ResolvedBlock(
                key="user_data",
                content="y" * 5000,
                metadata=BlockMetadata(user_block=True),
            ),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=500,
            count_tokens=len,
        ).stats
        assert stats.context_blocks_dropped == 1


class TestContextWindowStatsWithTokenCounter:
    """Tests using a custom token counter."""

    def test_token_counter_used_for_stats(self):
        """Stats use the provided token counter, not len()."""

        def word_counter(s: str) -> int:
            return len(s.split())

        blocks = [
            ResolvedBlock(key="sys", content="hello world foo"),
            ResolvedBlock(key="msg", content="one two", role=Role.USER),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=10000,
            count_tokens=word_counter,
        ).stats
        assert stats.context_blocks_tokens == 3  # "hello world foo" = 3 words
        assert stats.events_tokens == 2  # "one two" = 2 words
        assert stats.total_tokens == 5


class TestContextWindowStatsToolCallEvents:
    """ToolCallEvent blocks have content="" and contribute 0 to token counts."""

    def test_tool_call_event_counted_in_events_count_but_zero_tokens(self):
        """ToolCallEvent blocks count as events but contribute 0 tokens."""
        from nemo_oo_agents.context_blocks.events import ToolCallEvent, ToolResult

        event = ToolCallEvent(
            tool_call_id="tc_1",
            name="execute_python",
            arguments={"code": "1+1"},
            result=ToolResult(tool_call_id="tc_1", content="2"),
        )
        blocks = [
            ResolvedBlock(key="msg", content="hello", role=Role.USER),
            ResolvedBlock(key="tc", content="", role=Role.ASSISTANT, event=event),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.events_count == 2
        assert stats.events_tokens == len("hello")  # ToolCallEvent contributes 0


class TestContextWindowStatsEdgeCases:
    """Edge cases and regression tests."""

    def test_zero_utilization(self):
        """0% utilization when no content but limit is set."""
        stats = render_context(
            [],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        ).stats
        assert stats.context_utilization == 0.0

    def test_full_utilization(self):
        """100% utilization when content exactly at limit."""
        blocks = [ResolvedBlock(key="sys", content="x" * 1000)]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        ).stats
        assert stats.context_utilization == 1.0

    def test_zero_limit_returns_none_utilization(self):
        """Zero limit treated like None — returns None for utilization."""
        stats = ContextWindowStats(
            context_blocks_tokens=100,
            context_blocks_count=1,
            events_tokens=0,
            events_count=0,
            total_tokens=100,
            max_context_tokens=0,
            max_event_tokens=0,
        )
        assert stats.context_utilization is None
        assert stats.event_utilization is None

    def test_user_block_named_truncation_notice_no_false_positive(self):
        """A user block named 'truncation_notice' must not inflate context_blocks_dropped."""
        blocks = [
            ResolvedBlock(key="a", content="hello"),
            ResolvedBlock(key="truncation_notice", content="user data"),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=10000,
            count_tokens=len,
        ).stats
        assert stats.context_blocks_dropped == 0
        assert stats.context_blocks_count == 2

    def test_user_block_named_truncation_notice_no_false_positive_no_limit(self):
        """Without context_limit, a block named 'truncation_notice' causes no false positive."""
        blocks = [
            ResolvedBlock(key="truncation_notice", content="user data"),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        assert stats.context_blocks_dropped == 0

    def test_render_result_destructuring(self):
        """RenderResult supports tuple unpacking."""
        from nemo_oo_agents.context_blocks.models import RenderedMessage

        output, stats, messages = render_context(
            [ResolvedBlock(key="sys", content="hello")],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        )
        assert isinstance(output, list)
        assert isinstance(stats, ContextWindowStats)
        assert all(isinstance(m, RenderedMessage) for m in messages)

    def test_block_limit_no_longer_squashes_content(self):
        """Block-level head/tail truncation has been removed. ``block_limit``
        no longer caps individual blocks; the context_limit total-eviction
        path drops whole blocks instead. A single oversize block now exceeds
        the context_limit and gets dropped wholesale."""
        blocks = [
            ResolvedBlock(key="big", content="x" * 10000),
        ]
        stats = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        ).stats
        # Block exceeded context_limit and was dropped
        assert stats.context_blocks_dropped == 1

    def test_exports_from_context_blocks_package(self):
        """ContextWindowStats and RenderResult importable from context_blocks."""
        from nemo_oo_agents.context_blocks import ContextWindowStats as CWS
        from nemo_oo_agents.context_blocks import RenderResult as RR

        assert CWS is ContextWindowStats
        assert RR is RenderResult

    def test_exports_from_nemo_oo_agents_package(self):
        """ContextWindowStats importable from nemo_oo_agents."""
        from nemo_oo_agents import ContextWindowStats as CWS

        assert CWS is ContextWindowStats


class TestContextWindowStatsFormat:
    """Tests for the format() context block output."""

    def test_format_no_limits(self):
        """Without limits, shows totals only, no percentages on breakdown."""
        stats = ContextWindowStats(
            context_blocks_tokens=500,
            context_blocks_count=3,
            events_tokens=200,
            events_count=8,
            total_tokens=700,
        )
        text = stats.format()
        assert "Context usage: 700 tokens" in text
        assert "Context blocks: 500 tokens" in text
        assert "3 blocks" in text
        assert "Events:" in text
        assert "200 tokens" in text
        assert "8 events" in text
        # No percentages without any limits
        assert "%" not in text
        assert "self.events.collapse(start_tag, end_tag, summary_text=...)" in text

    def test_format_model_context_window(self):
        """With model_context_window but no per-category limits, total shows percentage."""
        stats = ContextWindowStats(
            context_blocks_tokens=8_200,
            context_blocks_count=6,
            events_tokens=4_250,
            events_count=18,
            total_tokens=12_450,
            model_context_window=200_000,
        )
        text = stats.format()
        assert "Context usage: 12,450 / 200,000 tokens (6.2%)" in text
        # No percentages on individual lines without per-category limits
        assert "Context blocks: 8,200 tokens" in text
        assert "Events:" in text
        assert "4,250 tokens" in text
        # Only one percentage (the total line)
        assert text.count("%") == 1

    def test_format_with_limits(self):
        """With limits, shows usage/limit and percentages."""
        stats = ContextWindowStats(
            context_blocks_tokens=8_200,
            context_blocks_count=6,
            events_tokens=4_250,
            events_count=18,
            total_tokens=12_450,
            max_context_tokens=32_000,
            max_event_tokens=20_000,
        )
        text = stats.format()
        assert "Context usage: 12,450 / 52,000 tokens" in text
        assert "Context blocks: 8,200 / 32,000 tokens (25.6%)" in text
        assert "6 blocks" in text
        assert "Events:" in text
        assert "4,250 / 20,000 tokens (21.2%)" in text
        assert "18 events" in text
        assert "self.events.collapse(start_tag, end_tag, summary_text=...)" in text

    def test_format_cleanup_guidance_when_hot(self):
        """Cleanup guidance remains visible when utilization exceeds 80%."""
        stats = ContextWindowStats(
            context_blocks_tokens=29_000,
            context_blocks_count=5,
            events_tokens=1_000,
            events_count=10,
            total_tokens=30_000,
            max_context_tokens=32_000,
            max_event_tokens=20_000,
        )
        text = stats.format()
        assert "self.events.collapse(start_tag, end_tag, summary_text=...)" in text
        assert "doc(self.events)" in text
        assert "self.context" in text
        assert "ContextApi" in text

    def test_format_cleanup_guidance_when_dropped(self):
        """Cleanup guidance remains visible when blocks or events were dropped."""
        stats = ContextWindowStats(
            context_blocks_tokens=500,
            context_blocks_count=2,
            events_tokens=200,
            events_count=5,
            total_tokens=700,
            max_context_tokens=10_000,
            max_event_tokens=10_000,
            context_blocks_dropped=1,
        )
        text = stats.format()
        assert "1 EVICTED" in text
        assert "self.events.collapse(start_tag, end_tag, summary_text=...)" in text

    def test_format_dropped_counts_shown(self):
        """Dropped counts appear in the breakdown lines."""
        stats = ContextWindowStats(
            context_blocks_tokens=500,
            context_blocks_count=2,
            events_tokens=200,
            events_count=5,
            total_tokens=700,
            context_blocks_dropped=3,
            events_dropped=7,
        )
        text = stats.format()
        assert "3 EVICTED" in text
        assert "7 dropped" in text

    def test_format_empty(self):
        """Empty stats produce a minimal output."""
        stats = ContextWindowStats(
            context_blocks_tokens=0,
            context_blocks_count=0,
            events_tokens=0,
            events_count=0,
            total_tokens=0,
        )
        text = stats.format()
        assert "Context usage: 0 tokens" in text
        assert "0 blocks" in text
        assert "0 events" in text


class TestContextWindowStatsFrozen:
    """ContextWindowStats is immutable."""

    def test_stats_are_frozen(self):
        """Cannot mutate stats after creation."""
        import pytest
        from pydantic import ValidationError

        stats = render_context(
            [],
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).stats
        with pytest.raises(ValidationError):
            stats.total_tokens = 999
