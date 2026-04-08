"""Tests for context_blocks formatters.

Tests BlockFormatter (XML, Markdown) and ProviderFormatter (OpenAI, Anthropic)
with the ResolvedBlock-based API.

BlockFormatter.format() takes list[ResolvedBlock] (system blocks only).
ProviderFormatter.format() takes (context_str, message_blocks) — no block_formatter param.
format_expr() has been removed from BlockFormatter (moved to render_context).
"""

import pytest

from context_blocks.events import ToolCallEvent, ToolResult
from context_blocks.formatter import (
    AnthropicProviderFormatter,
    MarkdownBlockFormatter,
    OpenAIProviderFormatter,
    XMLBlockFormatter,
)
from context_blocks.models import BlockMetadata, ResolvedBlock, Role


def _tool_call_block(
    *,
    key: str = "tc",
    tool_call_id: str,
    name: str,
    arguments: dict,
    result_content: str | None = None,
) -> ResolvedBlock:
    """Helper to create a ResolvedBlock with a ToolCallEvent."""
    result = None
    if result_content is not None:
        result = ToolResult(tool_call_id=tool_call_id, content=result_content)
    event = ToolCallEvent(
        tool_call_id=tool_call_id,
        name=name,
        arguments=arguments,
        result=result,
    )
    return ResolvedBlock(
        key=key,
        content="",
        role=Role.ASSISTANT,
        event=event,
    )


class TestBlockFormatterABC:
    """Tests for BlockFormatter abstract base class."""

    def test_is_abstract(self):
        """BlockFormatter should be abstract - cannot instantiate directly."""
        from context_blocks.formatter import BlockFormatter

        with pytest.raises(TypeError):
            BlockFormatter()  # type: ignore[abstract]

    def test_requires_format_method(self):
        """BlockFormatter subclasses must implement format() and format_type."""
        from context_blocks.formatter import BlockFormatter

        class IncompleteFormatter(BlockFormatter):
            pass

        with pytest.raises(TypeError):
            IncompleteFormatter()  # type: ignore[abstract]


class TestXMLBlockFormatter:
    """Tests for XMLBlockFormatter."""

    def test_single_block(self):
        """XMLBlockFormatter should wrap single block in XML tags."""
        formatter = XMLBlockFormatter()
        blocks = [ResolvedBlock(key="persona", content="You are helpful.")]
        result = formatter.format(blocks)

        assert "<persona>" in result
        assert "</persona>" in result
        assert "You are helpful." in result

    def test_multiple_blocks(self):
        """XMLBlockFormatter should wrap multiple blocks, separated by newlines."""
        formatter = XMLBlockFormatter()
        blocks = [
            ResolvedBlock(key="persona", content="You are helpful."),
            ResolvedBlock(key="tools", content="Available tools: search, calculate"),
        ]
        result = formatter.format(blocks)

        assert "<persona>" in result
        assert "</persona>" in result
        assert "<tools>" in result
        assert "</tools>" in result
        assert "You are helpful." in result
        assert "Available tools:" in result

    def test_empty_blocks(self):
        """XMLBlockFormatter should handle empty blocks list."""
        formatter = XMLBlockFormatter()
        result = formatter.format([])
        assert result == ""

    def test_preserves_content_newlines(self):
        """XMLBlockFormatter should preserve newlines in content."""
        formatter = XMLBlockFormatter()
        blocks = [ResolvedBlock(key="content", content="Line 1\nLine 2\nLine 3")]
        result = formatter.format(blocks)

        assert "Line 1\nLine 2\nLine 3" in result

    def test_with_metadata_expr(self):
        """XMLBlockFormatter should include expr attribute from metadata."""
        formatter = XMLBlockFormatter()
        blocks = [
            ResolvedBlock(
                key="notes",
                content="My notes",
                metadata=BlockMetadata(expr="self.context['notes']"),
            )
        ]
        result = formatter.format(blocks)

        assert "expr=\"self.context['notes']\"" in result
        assert "My notes" in result

    def test_format_type(self):
        """XMLBlockFormatter.format_type returns 'xml'."""
        formatter = XMLBlockFormatter()
        assert formatter.format_type == "xml"


class TestMarkdownBlockFormatter:
    """Tests for MarkdownBlockFormatter."""

    def test_single_block(self):
        """MarkdownBlockFormatter should format with markdown header."""
        formatter = MarkdownBlockFormatter()
        blocks = [ResolvedBlock(key="persona", content="You are helpful.")]
        result = formatter.format(blocks)

        assert "# Persona" in result
        assert "You are helpful." in result

    def test_multiple_blocks(self):
        """MarkdownBlockFormatter should format multiple blocks with headers."""
        formatter = MarkdownBlockFormatter()
        blocks = [
            ResolvedBlock(key="persona", content="You are helpful."),
            ResolvedBlock(key="tools", content="Available tools"),
        ]
        result = formatter.format(blocks)

        assert "# Persona" in result
        assert "# Tools" in result

    def test_key_with_underscores(self):
        """MarkdownBlockFormatter should convert underscores to spaces in headers."""
        formatter = MarkdownBlockFormatter()
        blocks = [ResolvedBlock(key="python_tools", content="Tool list")]
        result = formatter.format(blocks)

        assert "# Python Tools" in result

    def test_empty_blocks(self):
        """MarkdownBlockFormatter should handle empty blocks list."""
        formatter = MarkdownBlockFormatter()
        result = formatter.format([])
        assert result == ""

    def test_with_metadata(self):
        """MarkdownBlockFormatter should include metadata inline."""
        formatter = MarkdownBlockFormatter()
        blocks = [
            ResolvedBlock(
                key="notes",
                content="My notes",
                metadata=BlockMetadata(expr="self.context['notes']"),
            )
        ]
        result = formatter.format(blocks)

        assert "# Notes" in result
        assert '"expr"' in result
        assert "My notes" in result

    def test_format_type(self):
        """MarkdownBlockFormatter.format_type returns 'markdown'."""
        formatter = MarkdownBlockFormatter()
        assert formatter.format_type == "markdown"


class TestProviderFormatterABC:
    """Tests for ProviderFormatter abstract base class."""

    def test_is_abstract(self):
        """ProviderFormatter should be abstract - cannot instantiate directly."""
        from context_blocks.formatter import ProviderFormatter

        with pytest.raises(TypeError):
            ProviderFormatter()  # type: ignore[abstract]


class TestOpenAIProviderFormatter:
    """Tests for OpenAIProviderFormatter with ResolvedBlock API."""

    def test_system_message_only(self):
        """OpenAIProviderFormatter should create system message from context."""
        formatter = OpenAIProviderFormatter()
        result = formatter.format("You are helpful.", [])

        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_user_message(self):
        """OpenAIProviderFormatter should handle USER role blocks."""
        formatter = OpenAIProviderFormatter()
        blocks = [ResolvedBlock(key="msg", content="Hello", role=Role.USER)]
        result = formatter.format("System", blocks)

        assert len(result) == 2
        assert result[1]["role"] == "user"
        assert "Hello" in result[1]["content"]

    def test_assistant_message(self):
        """OpenAIProviderFormatter should handle ASSISTANT role blocks."""
        formatter = OpenAIProviderFormatter()
        blocks = [ResolvedBlock(key="msg", content="Hi there!", role=Role.ASSISTANT)]
        result = formatter.format("System", blocks)

        assert len(result) == 2
        assert result[1]["role"] == "assistant"
        assert "Hi there!" in result[1]["content"]

    def test_tool_call_message(self):
        """OpenAIProviderFormatter should handle ToolCallEvent on block.event."""
        formatter = OpenAIProviderFormatter()
        blocks = [
            _tool_call_block(
                tool_call_id="call_abc",
                name="get_weather",
                arguments={"location": "SF"},
            )
        ]
        result = formatter.format("System", blocks)

        assert len(result) == 2
        msg = result[1]
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["id"] == "call_abc"
        assert msg["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_tool_call_with_result(self):
        """OpenAIProviderFormatter should expand tool_call + tool_result into two messages."""
        formatter = OpenAIProviderFormatter()
        blocks = [
            _tool_call_block(
                tool_call_id="call_abc",
                name="get_weather",
                arguments={"location": "SF"},
                result_content="Sunny",
            )
        ]
        result = formatter.format("System", blocks)

        # system + assistant(tool_call) + tool(result)
        assert len(result) == 3
        assert result[1]["role"] == "assistant"
        assert "tool_calls" in result[1]
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "call_abc"
        assert result[2]["content"] == "Sunny"

    def test_full_conversation(self):
        """OpenAIProviderFormatter should handle full conversation with tool calls."""
        formatter = OpenAIProviderFormatter()
        blocks = [
            ResolvedBlock(key="q", content="What's the weather?", role=Role.USER),
            _tool_call_block(
                key="tc",
                tool_call_id="tc_1",
                name="weather",
                arguments={"loc": "SF"},
                result_content="Sunny",
            ),
            ResolvedBlock(key="a", content="It's sunny in SF.", role=Role.ASSISTANT),
        ]
        result = formatter.format("You are a weather bot.", blocks)

        # system + user + assistant(tool_call) + tool(result) + assistant
        assert len(result) == 5
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "assistant"

    def test_runtime_event_skipped(self):
        """OpenAIProviderFormatter should skip RUNTIME_EVENT role blocks."""
        formatter = OpenAIProviderFormatter()
        blocks = [
            ResolvedBlock(key="msg", content="Hello", role=Role.USER),
            ResolvedBlock(key="evt", content="internal", role=Role.RUNTIME_EVENT),
        ]
        result = formatter.format("System", blocks)

        assert len(result) == 2  # system + user only
        assert result[1]["role"] == "user"

    def test_metadata_skipped(self):
        """OpenAIProviderFormatter should skip METADATA role blocks (stored metadata, not shown to LLM)."""
        formatter = OpenAIProviderFormatter()
        blocks = [
            ResolvedBlock(key="msg", content="Hello", role=Role.USER),
            ResolvedBlock(key="meta", content="session-start", role=Role.METADATA),
        ]
        result = formatter.format("System", blocks)

        assert len(result) == 2  # system + user only; metadata is excluded
        assert result[1]["role"] == "user"

    def test_metadata_subclass_skipped(self):
        """Metadata subclasses (like TUISessionStart) should not appear in LLM messages."""
        from typing import ClassVar, Literal

        from pydantic import Field

        from context_blocks import Metadata
        from context_blocks.roles import Role as _Role

        class FakeSessionStart(Metadata):
            event_type: Literal["fake_session_start"] = Field(default="fake_session_start", repr=False)
            _role: ClassVar[_Role] = _Role.METADATA
            model: str = "test-model"

        event = FakeSessionStart(model="openai/gpt-4o")
        formatter = OpenAIProviderFormatter()
        blocks = [
            ResolvedBlock(key="user_msg", content="Hello", role=Role.USER),
            ResolvedBlock(key="sess", content=str(event), role=Role.METADATA, event=event),
        ]
        result = formatter.format("System", blocks)

        # Only system + user message; the metadata event must not reach the LLM
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        # Verify no metadata content leaked into any message
        combined = " ".join(str(m.get("content", "")) for m in result)
        assert "fake_session_start" not in combined
        assert "openai/gpt-4o" not in combined


class TestAnthropicProviderFormatter:
    """Tests for AnthropicProviderFormatter with ResolvedBlock API."""

    def test_returns_dict_with_system_and_messages(self):
        """AnthropicProviderFormatter should return dict with system and messages."""
        formatter = AnthropicProviderFormatter()
        result = formatter.format("You are helpful.", [])

        assert isinstance(result, dict)
        assert result["system"] == "You are helpful."
        assert result["messages"] == []

    def test_user_message(self):
        """AnthropicProviderFormatter should handle USER role blocks."""
        formatter = AnthropicProviderFormatter()
        blocks = [ResolvedBlock(key="msg", content="Hello", role=Role.USER)]
        result = formatter.format("System", blocks)

        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Hello"

    def test_assistant_message(self):
        """AnthropicProviderFormatter should handle ASSISTANT role blocks."""
        formatter = AnthropicProviderFormatter()
        blocks = [ResolvedBlock(key="msg", content="Hi!", role=Role.ASSISTANT)]
        result = formatter.format("System", blocks)

        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "assistant"
        assert result["messages"][0]["content"] == "Hi!"

    def test_tool_call_message(self):
        """AnthropicProviderFormatter should format tool_call as tool_use."""
        formatter = AnthropicProviderFormatter()
        blocks = [
            _tool_call_block(
                tool_call_id="tc_1",
                name="search",
                arguments={"q": "test"},
            )
        ]
        result = formatter.format("System", blocks)

        msg = result["messages"][0]
        assert msg["role"] == "assistant"
        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "tool_use"
        assert msg["content"][0]["id"] == "tc_1"

    def test_tool_call_with_result(self):
        """AnthropicProviderFormatter should expand tool_call + result."""
        formatter = AnthropicProviderFormatter()
        blocks = [
            _tool_call_block(
                tool_call_id="tc_1",
                name="search",
                arguments={"q": "test"},
                result_content="Result",
            )
        ]
        result = formatter.format("System", blocks)

        # assistant (tool_use) + user (tool_result)
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "assistant"
        assert result["messages"][1]["role"] == "user"
        assert result["messages"][1]["content"][0]["type"] == "tool_result"

    def test_tool_role_mapped_to_user(self):
        """AnthropicProviderFormatter should map TOOL role to user."""
        formatter = AnthropicProviderFormatter()
        blocks = [ResolvedBlock(key="tr", content="Tool output", role=Role.TOOL)]
        result = formatter.format("System", blocks)

        assert result["messages"][0]["role"] == "user"

    def test_metadata_skipped(self):
        """AnthropicProviderFormatter should skip METADATA role blocks."""
        formatter = AnthropicProviderFormatter()
        blocks = [
            ResolvedBlock(key="msg", content="Hello", role=Role.USER),
            ResolvedBlock(key="meta", content="session-start", role=Role.METADATA),
        ]
        result = formatter.format("System", blocks)

        assert len(result["messages"]) == 1  # metadata excluded
        assert result["messages"][0]["role"] == "user"

    def test_metadata_subclass_skipped(self):
        """Metadata subclasses should not appear in Anthropic LLM messages."""
        from typing import ClassVar, Literal

        from pydantic import Field

        from context_blocks import Metadata
        from context_blocks.roles import Role as _Role

        class FakeRename(Metadata):
            event_type: Literal["fake_rename"] = Field(default="fake_rename", repr=False)
            _role: ClassVar[_Role] = _Role.METADATA
            name: str = ""

        event = FakeRename(name="My Session")
        formatter = AnthropicProviderFormatter()
        blocks = [
            ResolvedBlock(key="user_msg", content="Hello", role=Role.USER),
            ResolvedBlock(key="rename", content=str(event), role=Role.METADATA, event=event),
        ]
        result = formatter.format("System", blocks)

        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        combined = " ".join(str(m.get("content", "")) for m in result["messages"])
        assert "fake_rename" not in combined
        assert "My Session" not in combined


class TestFormatterComposition:
    """Tests for composing BlockFormatter with ProviderFormatter."""

    def test_xml_with_openai(self):
        """XML blocks + OpenAI provider should work together."""
        block_formatter = XMLBlockFormatter()
        provider_formatter = OpenAIProviderFormatter()

        system_blocks = [
            ResolvedBlock(key="persona", content="You are helpful."),
            ResolvedBlock(key="tools", content="search, calculate"),
        ]
        context_str = block_formatter.format(system_blocks)

        message_blocks = [ResolvedBlock(key="msg", content="Hello", role=Role.USER)]
        result = provider_formatter.format(context_str, message_blocks)

        assert len(result) == 2
        assert "<persona>" in result[0]["content"]
        assert "Hello" in result[1]["content"]

    def test_markdown_with_anthropic(self):
        """Markdown blocks + Anthropic provider should work together."""
        block_formatter = MarkdownBlockFormatter()
        provider_formatter = AnthropicProviderFormatter()

        system_blocks = [ResolvedBlock(key="persona", content="You are helpful.")]
        context_str = block_formatter.format(system_blocks)

        message_blocks = [ResolvedBlock(key="msg", content="Hello", role=Role.USER)]
        result = provider_formatter.format(context_str, message_blocks)

        assert "# Persona" in result["system"]
        assert result["messages"][0]["content"] == "Hello"


class TestBlockFormatterFormatEvent:
    """Tests for BlockFormatter.format_event() — serializes raw events to content strings."""

    def test_xml_format_event_user_event(self):
        """XMLBlockFormatter.format_event() should call agentdoc_pformat on the event."""
        from context_blocks.events import UserEvent

        formatter = XMLBlockFormatter()
        event = UserEvent(content="Hello world", tag="1")
        result = formatter.format_event(event)

        # Should contain the content field (pformat repr of UserEvent)
        assert "Hello world" in result

    def test_markdown_format_event_user_event(self):
        """MarkdownBlockFormatter.format_event() should call agentdoc_pformat on the event."""
        from context_blocks.events import UserEvent

        formatter = MarkdownBlockFormatter()
        event = UserEvent(content="Hello world", tag="1")
        result = formatter.format_event(event)

        assert "Hello world" in result

    def test_format_event_has_default(self):
        """BlockFormatter provides a concrete format_event() default (agentdoc pformat)."""
        from context_blocks.formatter import BlockFormatter, FormatType

        class MinimalFormatter(BlockFormatter):
            @property
            def format_type(self):
                return FormatType.XML

            def format(self, blocks):
                return ""

            def format_description(self) -> str:
                return "minimal"

        from context_blocks.events import UserEvent

        formatter = MinimalFormatter()
        event = UserEvent(content="Hello world", tag="1")
        result = formatter.format_event(event)
        assert "Hello world" in result
