"""Formatters for context blocks and provider output.

Two orthogonal formatter types:
1. BlockFormatter - How to format system prompt blocks (XML, Markdown)
2. ProviderFormatter - How to assemble system prompt + messages for provider (OpenAI, Anthropic)

Compose any combination without combinatorial explosion.
"""

import json
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from context_blocks.events import EventBase, ToolCallEvent
from context_blocks.models import ResolvedBlock, Role
from context_blocks.utils import _MAX_PRE_FORMAT_CHARS, safe_pformat


class FormatType(StrEnum):
    """Format type for block and message rendering.

    StrEnum so values work as plain strings (e.g., in f-strings, == comparisons).
    """

    XML = "xml"
    MARKDOWN = "markdown"
    PLAIN = "plain"


# Convenience aliases
FORMAT_XML = FormatType.XML
FORMAT_MARKDOWN = FormatType.MARKDOWN
FORMAT_PLAIN = FormatType.PLAIN


class BlockFormatter(ABC):
    """Abstract base class for formatting system prompt blocks into a string.

    Subclasses implement format() to convert a list of ResolvedBlocks into
    a formatted string (e.g., XML tags, Markdown headers).
    """

    @property
    @abstractmethod
    def format_type(self) -> FormatType:
        """Return format type identifier (FormatType.XML, FormatType.MARKDOWN, FormatType.PLAIN)."""
        ...

    @abstractmethod
    def format(self, blocks: list[ResolvedBlock]) -> str:
        """Format system prompt blocks into context string.

        Args:
            blocks: List of resolved system blocks with content and metadata.

        Returns:
            Formatted string combining all blocks.
        """
        ...

    @abstractmethod
    def format_description(self) -> str:
        """Return a human-readable description of the block format.

        Used in the agent system prompt so the LLM understands how
        context blocks are structured.
        """
        ...

    def format_event(self, event: EventBase, max_chars: int = _MAX_PRE_FORMAT_CHARS) -> str:
        """Serialize a non-tool event into a content string.

        Called by render_context() for each non-ToolCallEvent message block
        before truncation. The result becomes block.content.

        Default: bounded repr via safe_pformat. Override in subclasses
        (e.g. PlainBlockFormatter) for alternative serialization.

        Args:
            event:     The raw event to serialize.
            max_chars: Hard character cap on pformat output (safety net before
                       block-level truncation).  Comes from
                       ``TruncationConfig.max_pre_format_chars`` via render_context().

        Returns:
            String content to use as the block's message content.
        """
        return safe_pformat(event, max_chars=max_chars)


class XMLBlockFormatter(BlockFormatter):
    """Wraps each block in XML tags with metadata attributes.

    Example:
        <notes expr="self.context['notes']">
        Here are my notes...
        </notes>
    """

    @property
    def format_type(self) -> FormatType:
        return FORMAT_XML

    def format_description(self) -> str:
        return (
            'Your prompt is organized in XML context blocks: `<name expr="expression">CONTENT</name>`.\n'
            "The `expr` attribute is the Python expression that produced the content."
        )

    def format(self, blocks: list[ResolvedBlock]) -> str:
        if not blocks:
            return ""

        parts = []
        for block in blocks:
            attr_parts = []
            if block.metadata.expr:
                attr_parts.append(f'expr="{block.metadata.expr}"')
            attrs = (" " + " ".join(attr_parts)) if attr_parts else ""
            parts.append(f"<{block.key}{attrs}>\n{block.content}\n</{block.key}>")
        return "\n\n".join(parts)


class MarkdownBlockFormatter(BlockFormatter):
    """Formats blocks with markdown headers and inline metadata.

    Example:
        # Notes `{"expr": "self.context['notes']"}`

        Here are my notes...
    """

    @property
    def format_type(self) -> FormatType:
        return FORMAT_MARKDOWN

    def format_description(self) -> str:
        return (
            "Your prompt is organized in context blocks with markdown headers: "
            '`# Block Name {"expr": "expression"}`.\n'
            "The inline JSON contains the Python expression that produced the content."
        )

    def format(self, blocks: list[ResolvedBlock]) -> str:
        if not blocks:
            return ""

        parts = []
        for block in blocks:
            header = block.key.replace("_", " ").title()

            inline_meta = ""
            if block.metadata.expr:
                dict_parts = [f'"expr": "{block.metadata.expr}"']
                inline_meta = " `{" + ", ".join(dict_parts) + "}`"

            parts.append(f"# {header}{inline_meta}\n\n{block.content}")
        return "\n\n".join(parts)


class ProviderFormatter(ABC):
    """Abstract base class for assembling system prompt + messages into provider output.

    Message blocks arrive pre-formatted (content already wrapped with metadata).
    The provider formatter only needs to handle message assembly and tool calls.

    Tool call blocks carry the original ToolCallEvent on ``block.event``,
    which the formatter reads directly for structured fields.
    """

    @abstractmethod
    def format(
        self,
        context: str,
        message_blocks: list[ResolvedBlock],
    ) -> Any:
        """Format context string + message blocks into provider-specific output.

        Args:
            context: Formatted system prompt string (from BlockFormatter).
            message_blocks: Pre-formatted message blocks. Non-tool-call blocks
                have content already wrapped with metadata. Tool-call blocks
                carry the original ToolCallEvent on block.event.

        Returns:
            Provider-specific output (type depends on implementation).
        """
        ...


class OpenAIProviderFormatter(ProviderFormatter):
    """Assembles context + messages into OpenAI message format."""

    def format(
        self,
        context: str,
        message_blocks: list[ResolvedBlock],
    ) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": context}]

        for block in message_blocks:
            if block.role in (Role.RUNTIME_EVENT, Role.METADATA):
                continue

            if isinstance(block.event, ToolCallEvent):
                event = block.event
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": event.tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": event.name,
                                    "arguments": json.dumps(event.arguments),
                                },
                            }
                        ],
                    }
                )
                if event.result is not None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": event.tool_call_id,
                            "content": event.result.content,
                        }
                    )
            else:
                # Check for image attachments (from show() in CodeAct execution)
                images = getattr(block.event, "images", None) if block.event is not None else None
                if images:
                    _append_images(messages, block.role.value, block.content, images)
                else:
                    messages.append({"role": block.role.value, "content": block.content or ""})

        return messages


def _append_images(messages: list[dict], role: str, content: str, images: list[dict]) -> None:
    """Append a message with multi-part content (text + images) to the message list.

    Images use LiteLLM's universal image_url format. LiteLLM automatically
    converts to provider-native formats (Anthropic, Bedrock, Vertex AI, etc.).
    See: https://docs.litellm.ai/docs/completion/vision
    """
    content_parts: list[dict] = [{"type": "text", "text": content or ""}]
    content_parts.extend(images)
    messages.append({"role": role, "content": content_parts})


class AnthropicProviderFormatter(ProviderFormatter):
    """Assembles context + messages into Anthropic message format."""

    def format(
        self,
        context: str,
        message_blocks: list[ResolvedBlock],
    ) -> dict:
        messages: list[dict] = []

        for block in message_blocks:
            if block.role in (Role.RUNTIME_EVENT, Role.METADATA):
                continue

            if isinstance(block.event, ToolCallEvent):
                event = block.event
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": event.tool_call_id,
                                "name": event.name,
                                "input": event.arguments,
                            }
                        ],
                    }
                )
                if event.result is not None:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": event.tool_call_id,
                                    "content": event.result.content,
                                }
                            ],
                        }
                    )
            else:
                role = block.role
                if role not in (Role.USER, Role.ASSISTANT):
                    role = Role.USER
                # Check for image attachments (from show() in CodeAct execution)
                # Uses LiteLLM's universal image_url format — no provider-specific
                # conversion needed. LiteLLM handles Anthropic/Bedrock/Vertex conversion.
                images = getattr(block.event, "images", None) if block.event is not None else None
                if images:
                    _append_images(messages, role.value, block.content, images)
                else:
                    messages.append({"role": role.value, "content": block.content or ""})

        return {"system": context, "messages": messages}
