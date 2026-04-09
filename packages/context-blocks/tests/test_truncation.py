# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for context block truncation."""

from context_blocks.formatter import (
    MarkdownBlockFormatter,
    ProviderFormatter,
    XMLBlockFormatter,
)
from context_blocks.models import ResolvedBlock, Role
from context_blocks.renderer import render_context
from context_blocks.utils import truncate_content


class TestTruncateContent:
    """Tests for the truncate_content helper function."""

    def test_no_truncation_when_under_limit(self):
        """Value under limit should not be truncated."""
        value = "Hello world"
        result, was_truncated = truncate_content(value, 1000, "xml")

        assert result == value
        assert was_truncated is False

    def test_no_truncation_when_at_limit(self):
        """Value exactly at limit should not be truncated."""
        value = "x" * 100
        result, was_truncated = truncate_content(value, 100, "xml")

        assert result == value
        assert was_truncated is False

    def test_truncation_when_over_limit(self):
        """Value over limit should be truncated with prose notice."""
        value = "x" * 150
        result, was_truncated = truncate_content(value, 100, "xml")

        assert was_truncated is True
        assert len(result) > 100  # Notice + content
        # Prose format: head (50) + tail (50) = 100 x's shown
        assert "Output too large (150 chars)" in result
        assert "Showing first 50 and last 50 chars" in result
        assert result.count("x") == 100  # head + tail = limit

    def test_xml_truncation_notice_format(self):
        """format_type is ignored; all truncation uses the same prose notice."""
        value = "a" * 200
        result, was_truncated = truncate_content(value, 100, "xml")

        assert was_truncated is True
        assert "Output too large" in result
        assert result.startswith("Output too large")

    def test_markdown_truncation_notice_format(self):
        """format_type is ignored; markdown and xml produce identical prose notice."""
        value = "b" * 200
        result, was_truncated = truncate_content(value, 100, "markdown")

        assert was_truncated is True
        assert "Output too large" in result
        assert result.startswith("Output too large")


class TestRenderContextTruncation:
    """Tests for truncation in render_context()."""

    def test_no_truncation_without_limit(self):
        """Blocks should not be truncated when no limit is specified."""
        data = "x" * 50000
        blocks = [ResolvedBlock(key="test", content=data)]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
        )

        assert "x" * 50000 in result["context"]
        assert "truncation" not in result["context"].lower()

    def test_truncation_applied_with_limit(self):
        """Blocks exceeding limit should be truncated with prose notice."""
        data = "y" * 30000
        blocks = [ResolvedBlock(key="test", content=data)]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=20000,
        )

        # Prose format: "Output too large (30,000 chars). Showing first 10,000 and last 10,000 chars."
        assert "Output too large (30,000 chars)" in result["context"]
        assert "Showing first 10,000 and last 10,000 chars" in result["context"]
        assert result["context"].count("y") == 20000  # head(10k) + tail(10k)

    def test_xml_formatter_truncation_format(self):
        """Truncation notice appears inside the XML block tags."""
        long_text = "Lorem ipsum " * 2000
        blocks = [ResolvedBlock(key="persona", content=long_text)]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=1000,
        )

        context = result["context"]
        assert "<persona" in context
        assert "Output too large" in context
        assert "</persona>" in context

        # Notice must appear inside the XML block
        persona_start = context.find("<persona")
        notice_start = context.find("Output too large")
        persona_end = context.find("</persona>")
        assert persona_start < notice_start < persona_end

    def test_markdown_formatter_truncation_format(self):
        """Truncation notice appears in markdown-wrapped block."""
        long_text = "Instructions: " * 2000
        blocks = [ResolvedBlock(key="system_prompt", content=long_text)]

        result = render_context(
            blocks,
            block_formatter=MarkdownBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=1000,
        )

        assert "Output too large" in result["context"]
        assert "chars not shown" in result["context"]

    def test_multiple_blocks_truncated_independently(self):
        """Each block should be truncated independently."""
        blocks = [
            ResolvedBlock(key="block1", content="a" * 15000),
            ResolvedBlock(key="block2", content="b" * 500),
            ResolvedBlock(key="block3", content="c" * 25000),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=10000,
        )

        context = result["context"]

        # block1 should be truncated
        assert "Output too large (15,000 chars)" in context
        # block2 should NOT be truncated
        block2_start = context.find("<block2")
        block2_end = context.find("</block2>")
        block2_content = context[block2_start:block2_end]
        assert "Output too large" not in block2_content
        assert "b" * 500 in block2_content
        # block3 should be truncated
        assert "Output too large (25,000 chars)" in context


class TestEventBlockTruncation:
    """Tests for event/message block truncation."""

    def test_event_block_truncated_with_limit(self):
        """Message blocks exceeding block_limit should be truncated."""
        long_content = "x" * 5000
        blocks = [
            ResolvedBlock(key="event_1", content=long_content, role=Role.USER),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=1000,
        )

        msg = result["messages"][0]
        assert msg.content.count("x") == 1000  # head(500) + tail(500)
        assert "output too large" in msg.content.lower()
        assert msg.metadata.truncated is True

    def test_event_block_no_truncation_when_under_limit(self):
        """Short message blocks should not be truncated."""
        blocks = [
            ResolvedBlock(key="event_1", content="short", role=Role.USER),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=1000,
        )

        msg = result["messages"][0]
        assert msg.content == "short"
        assert msg.metadata.truncated is False

    def test_block_limit_applies_to_both_context_and_events(self):
        """block_limit truncates both context blocks and event blocks."""
        blocks = [
            ResolvedBlock(key="sys", content="x" * 5000, role=Role.SYSTEM),
            ResolvedBlock(key="event_1", content="y" * 5000, role=Role.USER),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=2000,
        )

        # System block truncated at 2000 (head=1000, tail=1000)
        assert "Output too large (5,000 chars)" in result["context"]
        assert result["context"].count("x") == 2000

        # Event block also truncated at 2000
        msg = result["messages"][0]
        assert msg.content.count("y") == 2000


class TestContextTotalLimit:
    """Tests for context_limit — total budget for all system blocks.

    When context exceeds the budget, blocks are dropped from the end
    (lowest priority) and a summary block is added listing what was dropped.
    """

    def test_context_limit_drops_from_end(self):
        """When over budget, blocks are dropped from the end first."""
        blocks = [
            ResolvedBlock(key="small", content="#" * 100),
            ResolvedBlock(key="large", content="$" * 5000),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        )

        context = result["context"]
        # Small block survives (it's first)
        assert "#" * 100 in context
        # Large block is dropped (it's at the end)
        assert "$" * 5000 not in context
        # Summary block present
        assert "dropped" in context.lower()
        assert "large" in context
        assert "5,000 chars" in context

    def test_context_limit_no_effect_when_under_budget(self):
        """context_limit should not drop blocks when total is under budget."""
        blocks = [
            ResolvedBlock(key="p1", content="#" * 100),
            ResolvedBlock(key="p2", content="$" * 100),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=10000,
            count_tokens=len,
        )

        context = result["context"]
        assert context.count("#") == 100
        assert context.count("$") == 100
        assert "dropped" not in context.lower()

    def test_context_limit_summary_includes_expr(self):
        """Dropped block summary includes the expr metadata."""
        from context_blocks.models import BlockMetadata

        blocks = [
            ResolvedBlock(key="small", content="#" * 100),
            ResolvedBlock(
                key="big_data",
                content="$" * 5000,
                metadata=BlockMetadata(expr="self.context['big_data']"),
            ),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        )

        context = result["context"]
        assert "big_data" in context
        assert "self.context['big_data']" in context
        assert "5,000 chars" in context

    def test_context_limit_includes_free_up_guidance(self):
        """Dropped block summary includes guidance on how to free up space."""
        blocks = [
            ResolvedBlock(key="small", content="#" * 100),
            ResolvedBlock(key="big", content="$" * 5000),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        )

        context = result["context"]
        assert "self.context.pop(" in context
        assert "self.context[" in context

    def test_context_limit_drops_multiple_blocks(self):
        """When budget is tight, multiple blocks are dropped from the end."""
        blocks = [
            ResolvedBlock(key="tiny", content="#" * 50),
            ResolvedBlock(key="medium", content="$" * 3000),
            ResolvedBlock(key="huge", content="@" * 8000),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=500,
            count_tokens=len,
        )

        context = result["context"]
        # Only tiny block survives (first in order)
        assert "#" * 50 in context
        # Both medium and huge are dropped (from the end)
        assert "medium" in context  # in summary
        assert "huge" in context  # in summary
        assert "3,000 chars" in context
        assert "8,000 chars" in context

    def test_block_limit_applied_before_context_limit(self):
        """block_limit truncates individual blocks before context_limit checks total."""
        blocks = [
            ResolvedBlock(key="p1", content="#" * 10000),
            ResolvedBlock(key="p2", content="$" * 10000),
        ]

        # block_limit=3000 truncates each to 3000 → total ~6000
        # context_limit=20000 → no blocks need to be dropped
        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            block_limit=3000,
            context_limit=20000,
            count_tokens=len,
        )

        context = result["context"]
        assert context.count("#") == 3000
        assert context.count("$") == 3000
        assert "dropped" not in context.lower()

    def test_context_limit_preserves_block_order(self):
        """Surviving blocks maintain their original order."""
        blocks = [
            ResolvedBlock(key="first", content="AAA"),
            ResolvedBlock(key="second", content="BBB"),
            ResolvedBlock(key="dropped", content="C" * 5000),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=1000,
            count_tokens=len,
        )

        context = result["context"]
        first_pos = context.find("<first")
        second_pos = context.find("<second")
        assert first_pos < second_pos
        # "dropped" block was at the end and got dropped
        assert "C" * 5000 not in context


class TestEventTotalLimit:
    """Tests for event_limit — total budget for all events."""

    def test_event_limit_drops_oldest(self):
        """When events exceed event_limit, oldest are dropped first."""
        blocks = [
            ResolvedBlock(key="event_1", content="old_" + "x" * 3000, role=Role.USER),
            ResolvedBlock(key="event_2", content="mid_" + "y" * 3000, role=Role.ASSISTANT),
            ResolvedBlock(key="event_3", content="new_" + "z" * 3000, role=Role.USER),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            event_limit=7000,
            count_tokens=len,
        )

        messages = result["messages"]
        # Oldest event should be dropped (3004 chars), leaving mid + new (~6008)
        assert len(messages) == 2
        assert "mid_" in messages[0].content
        assert "new_" in messages[1].content

    def test_event_limit_no_effect_when_under_budget(self):
        """event_limit should not drop events when total is under budget."""
        blocks = [
            ResolvedBlock(key="event_1", content="hello", role=Role.USER),
            ResolvedBlock(key="event_2", content="world", role=Role.ASSISTANT),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            event_limit=10000,
            count_tokens=len,
        )

        messages = result["messages"]
        assert len(messages) == 2

    def test_event_limit_drops_all_but_last(self):
        """When budget is very tight, only the most recent event survives."""
        blocks = [
            ResolvedBlock(key="event_1", content="a" * 5000, role=Role.USER),
            ResolvedBlock(key="event_2", content="b" * 5000, role=Role.ASSISTANT),
            ResolvedBlock(key="event_3", content="c" * 100, role=Role.USER),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            event_limit=200,
            count_tokens=len,
        )

        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0].content.startswith("c")

    def test_event_limit_independent_of_context_limit(self):
        """event_limit and context_limit apply to their respective sections."""
        blocks = [
            ResolvedBlock(key="small_sys", content="x" * 2000, role=Role.SYSTEM),
            ResolvedBlock(key="big_sys", content="y" * 8000, role=Role.SYSTEM),
            ResolvedBlock(key="event_1", content="a" * 5000, role=Role.USER),
            ResolvedBlock(key="event_2", content="b" * 5000, role=Role.ASSISTANT),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=MockProviderFormatter(),
            context_limit=5000,
            event_limit=6000,
            count_tokens=len,
        )

        context = result["context"]
        # Small system block survives, big one is dropped
        assert "x" * 2000 in context
        assert "y" * 8000 not in context
        assert "big_sys" in context  # in the dropped summary

        # One event should be dropped (oldest) to fit in 6000
        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0].content.startswith("b")


class MockProviderFormatter(ProviderFormatter):
    """Mock provider formatter for testing.

    Returns raw ResolvedBlock objects in 'messages' (unlike real formatters
    which return dicts). This lets tests assert on .content and .metadata directly.
    """

    def format(self, context: str, message_blocks: list) -> dict:
        """Return simple dict for testing."""
        return {"context": context, "messages": message_blocks}
