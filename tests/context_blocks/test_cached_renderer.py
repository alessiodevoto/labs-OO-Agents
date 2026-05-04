# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the cached renderer (immutable-prefix / events / volatile-suffix)."""

from nemo_oo_agents.context_blocks.events import AssistantEvent, UserEvent
from nemo_oo_agents.context_blocks.formatter import (
    AnthropicProviderFormatter,
    OpenAIProviderFormatter,
)
from nemo_oo_agents.context_blocks.models import BlockMetadata, DynamicContext, ResolvedBlock, Role
from nemo_oo_agents.context_blocks.renderer import render_context
from nemo_oo_agents.context_blocks.renderers.cached import CachedBlockFormatter


def _immutable_block(key: str, content: str, expr: str | None = None) -> ResolvedBlock:
    return ResolvedBlock(
        key=key,
        content=content,
        role=Role.SYSTEM,
        metadata=BlockMetadata(expr=expr, immutable=True),
    )


def _volatile_block(key: str, content: str, expr: str | None = None) -> ResolvedBlock:
    return ResolvedBlock(
        key=key,
        content=content,
        role=Role.SYSTEM,
        metadata=BlockMetadata(expr=expr, immutable=False, user_block=True),
    )


class TestImmutableMetadata:
    def test_block_metadata_has_immutable_field(self):
        meta = BlockMetadata(immutable=True)
        assert meta.immutable is True

    def test_block_metadata_immutable_defaults_false(self):
        assert BlockMetadata().immutable is False

    def test_dynamic_context_accepts_immutable(self):
        dc = DynamicContext("doc(self)", immutable=True)
        assert dc.immutable is True
        assert dc.expr == "doc(self)"

    def test_dynamic_context_immutable_defaults_false(self):
        assert DynamicContext("doc(self)").immutable is False

    def test_dynamic_context_repr_shows_immutable(self):
        assert "immutable=True" in repr(DynamicContext("x", immutable=True))
        assert "immutable" not in repr(DynamicContext("x"))


class TestCachedBlockFormatterPartition:
    def test_all_immutable_single_system_message(self):
        fmt = CachedBlockFormatter()
        messages = fmt.format([_immutable_block("a", "A"), _immutable_block("b", "B")])
        assert len(messages) == 1
        assert messages[0].role == Role.SYSTEM
        assert "<a>" in messages[0].content and "<b>" in messages[0].content

    def test_all_volatile_falls_back_to_system(self):
        """When no blocks are immutable, all go to SYSTEM (XMLBlockFormatter-compatible)."""
        fmt = CachedBlockFormatter()
        messages = fmt.format([_volatile_block("plan", "do stuff")])
        assert len(messages) == 1
        assert messages[0].role == Role.SYSTEM
        assert "<plan>" in messages[0].content
        assert "</plan>" in messages[0].content

    def test_mixed_preserves_order_within_halves(self):
        fmt = CachedBlockFormatter()
        messages = fmt.format(
            [
                _immutable_block("sys", "S"),
                _volatile_block("plan", "P"),
                _immutable_block("self_doc", "D"),
                _volatile_block("state", "T"),
            ]
        )
        assert len(messages) == 2
        sys_msg = messages[0]
        assert sys_msg.index("<sys>") < sys_msg.index("<self_doc>") if False else True  # sanity
        assert sys_msg.content.index("<sys>") < sys_msg.content.index("<self_doc>")
        user_msg = messages[1]
        assert user_msg.content.index("<plan>") < user_msg.content.index("<state>")


class TestCachedRendererEndToEndOpenAI:
    def test_immutable_becomes_system_message(self):
        # Non-dynamic-source block: ``expr=`` is suppressed even if provided
        # in metadata. Only ``self.context.set_dynamic()`` blocks render it.
        result = render_context(
            [_immutable_block("sys", "You are X.", expr="self._system_prompt()")],
            block_formatter=CachedBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).output
        assert result == [{"role": "system", "content": "<sys>\nYou are X.\n</sys>"}]

    def test_volatile_becomes_trailing_user(self):
        result = render_context(
            [
                _immutable_block("sys", "S"),
                _volatile_block("plan", "P", expr="self.context['plan']"),
            ],
            block_formatter=CachedBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).output
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "<sys>" in result[0]["content"]
        assert result[1]["role"] == "user"
        assert "<context>" in result[1]["content"]
        assert "<plan" in result[1]["content"]

    def test_volatile_merges_into_trailing_user_event(self):
        user_event = UserEvent(content="hi")
        user_event.tag = "1"
        blocks = [
            _immutable_block("sys", "S"),
            _volatile_block("plan", "P"),
            ResolvedBlock(
                key="event_1",
                content="",
                role=Role.USER,
                metadata=BlockMetadata(tag="1"),
                event=user_event,
            ),
        ]
        result = render_context(
            blocks,
            block_formatter=CachedBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).output
        roles = [m["role"] for m in result]
        # system + one user (volatile merged into the single trailing user event)
        assert roles == ["system", "user"]
        user_content = result[1]["content"]
        assert "<context>" in user_content
        assert "<plan>" in user_content

    def test_volatile_appended_after_assistant(self):
        asst_event = AssistantEvent(content="done")
        asst_event.tag = "2"
        blocks = [
            _immutable_block("sys", "S"),
            _volatile_block("plan", "P"),
            ResolvedBlock(
                key="event_2",
                content="",
                role=Role.ASSISTANT,
                metadata=BlockMetadata(tag="2"),
                event=asst_event,
            ),
        ]
        result = render_context(
            blocks,
            block_formatter=CachedBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).output
        roles = [m["role"] for m in result]
        assert roles == ["system", "assistant", "user"]
        assert "<context>" in result[2]["content"]

    def test_no_volatile_no_trailing_message(self):
        result = render_context(
            [_immutable_block("sys", "S")],
            block_formatter=CachedBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).output
        assert len(result) == 1 and result[0]["role"] == "system"

    def test_empty_input(self):
        result = render_context(
            [],
            block_formatter=CachedBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
        ).output
        assert result == []


class TestCachedRendererEndToEndAnthropic:
    def test_returns_system_and_messages_dict(self):
        result = render_context(
            [_immutable_block("sys", "S"), _volatile_block("plan", "P")],
            block_formatter=CachedBlockFormatter(),
            provider_formatter=AnthropicProviderFormatter(),
        ).output
        assert isinstance(result, dict)
        assert "system" in result and "messages" in result
        assert "<sys>" in result["system"]
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        assert "<context>" in result["messages"][0]["content"]
