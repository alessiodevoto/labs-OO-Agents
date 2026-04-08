"""CodeActLite strategy - simplified CodeAct with clean message rendering.

A variant of CodeActStrategy that removes three sources of LLM confusion:

1. **Scoped events** — Only shows events from the current method call
2. **No XML tags on messages** — Messages render as plain text, not wrapped in
   `<user_message expr="..." tag="...">...</user_message>`
3. **No Python type wrappers** — Events render as plain text, not
   `Task(prompt='...')` but just the prompt content
4. **Inline tool results** — PythonOutput is merged into the tool response
   instead of appearing as a separate user message

System prompt blocks still use XML formatting (via XMLBlockFormatter).
Only conversation messages are simplified.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from context_blocks import ResolvedBlock, ToolCallEvent
from context_blocks.formatter import OpenAIProviderFormatter
from context_blocks.models import Role
from context_blocks.scoped import ScopedContext
from context_blocks.utils import _MAX_PRE_FORMAT_CHARS, safe_pformat
from nemo_oo_agents.events import (
    Error,
    Feedback,
    LLMOutput,
    Message,
    PythonOutput,
    Reasoning,
    Task,
)
from nemo_oo_agents.runtime.event_query import EventQuery
from nemo_oo_agents.strategies.codeact import CodeActStrategy

if TYPE_CHECKING:
    from nemo_oo_agents.strategies.base import RuntimeServices
    from nemo_oo_agents.strategies.current_call import CurrentCall

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plain-text event rendering
# ---------------------------------------------------------------------------


def plain_event_content(event: Any, max_chars: int = _MAX_PRE_FORMAT_CHARS) -> str:
    """Render an event as plain text — no type wrappers, no metadata.

    Extracts human-readable content from event objects instead of using
    `pformat(event)` which produces `Task(prompt='...')` style output.

    Args:
        event:     An event object (Task, Error, PythonOutput, etc.)
        max_chars: Hard character cap applied to pformat of non-string values
                   (e.g. Out[n] Python return values).  Comes from
                   TruncationConfig.max_pre_format_chars via PlainProviderFormatter.

    Returns:
        Plain text content suitable for LLM consumption.
    """
    # Task — use the prompt directly
    if isinstance(event, Task):
        return event.prompt

    # PythonOutput — format stdout/value/error cleanly
    if isinstance(event, PythonOutput):
        parts: list[str] = []
        if event.stdout:
            parts.append(event.stdout)
        if event.error:
            parts.append(f"Error: {event.error}")
        if event.stderr and not event.error:
            parts.append(f"Stderr: {event.stderr}")
        if event.value is not None:
            # safe_pformat handles non-strings; strings bypass pformat but must
            # still be capped so a 10 MB string return value doesn't create a
            # huge intermediate allocation before block-level truncation fires.
            value_str = safe_pformat(event.value, max_chars=max_chars)
            parts.append(f"Out[{event.execution_count}]: {value_str}")
        if event.captured_locals:
            parts.append(event.captured_locals)
        return "\n".join(parts) if parts else "(no output)"

    # Error, Message, Reasoning, LLMOutput, Feedback — use content directly
    if isinstance(event, (Error, Message, Reasoning, LLMOutput, Feedback)):
        return event.content

    # Fallback
    return str(event)


# ---------------------------------------------------------------------------
# PlainProviderFormatter — handles all three rendering changes
# ---------------------------------------------------------------------------


class PlainProviderFormatter(OpenAIProviderFormatter):
    """Provider formatter that renders clean messages without XML tags or type wrappers.

    Handles three changes compared to OpenAIProviderFormatter:
    1. Strips XML wrapping from message content (uses plain_event_content)
    2. Removes Python type wrappers (Task(...) -> just the prompt text)
    3. Merges PythonOutput into tool response (inline tool results)

    System prompt blocks are not affected — they still use XML formatting.

    Args:
        max_pre_format_chars: Hard character cap forwarded to plain_event_content
            for non-string Out[n] values.  Set from
            TruncationConfig.max_pre_format_chars by CodeActLiteStrategy.
    """

    def __init__(self, max_pre_format_chars: int = _MAX_PRE_FORMAT_CHARS):
        self._max_pre_format_chars = max_pre_format_chars

    def format(
        self,
        context: str,
        message_blocks: list[ResolvedBlock],
    ) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": context}]

        # Index PythonOutput blocks by tool_call_id for merging
        python_outputs: dict[str, ResolvedBlock] = {}
        for block in message_blocks:
            if isinstance(block.event, PythonOutput):
                python_outputs[block.event.tool_call_id] = block

        for block in message_blocks:
            # Skip runtime events
            if block.role == Role.RUNTIME_EVENT:
                continue

            # ToolCallEvent — render assistant tool_call + merged tool response
            if isinstance(block.event, ToolCallEvent):
                event = block.event

                # Assistant message with tool call
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

                # Tool response: merge PythonOutput content if available
                py_out_block = python_outputs.get(event.tool_call_id)
                if py_out_block and py_out_block.event:
                    content = plain_event_content(
                        py_out_block.event, max_chars=self._max_pre_format_chars
                    )
                elif event.result is not None:
                    content = event.result.content
                else:
                    content = ""

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": event.tool_call_id,
                        "content": content,
                    }
                )

            # PythonOutput — skip if already merged into tool response above
            elif isinstance(block.event, PythonOutput):
                if block.event.tool_call_id in python_outputs:
                    # Already merged into the ToolCallEvent response
                    continue
                # Orphan PythonOutput (shouldn't happen, but handle gracefully)
                content = plain_event_content(block.event, max_chars=self._max_pre_format_chars)
                messages.append({"role": "user", "content": content})

            # All other events — render as plain text
            else:
                if block.event is not None:
                    content = plain_event_content(block.event, max_chars=self._max_pre_format_chars)
                else:
                    content = block.content or ""
                messages.append({"role": block.role.value, "content": content})

        return messages


# ---------------------------------------------------------------------------
# CodeActLiteStrategy
# ---------------------------------------------------------------------------


class CodeActLiteStrategy(CodeActStrategy):
    """Simplified CodeAct strategy with clean message rendering.

    Extends CodeActStrategy with three changes:
    1. Events scoped to current call only (ScopedContext + EventQuery.current_call())
    2. Messages rendered as plain text (no XML tags, no Python type wrappers)
    3. Tool results inlined (PythonOutput merged into tool response)

    Usage:
        from nemo_oo_agents.strategies.codeact_lite import CodeActLiteStrategy

        @strategy(CodeActLiteStrategy())
        async def my_method(self, x: str) -> str:
            '''Classify x.'''
            ...
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "CODEACT_LITE"

    async def execute(self, runtime: "RuntimeServices", call: "CurrentCall") -> Any:
        """Execute with scoped events and plain message formatting.

        Wraps the parent CodeActStrategy.execute() with:
        1. ScopedContext to limit events to current call
        2. Temporary formatter swap to PlainProviderFormatter

        Note: We use the explicit call_id from the agent call stack rather than
        EventQuery.current_call() because CodeActStrategy.execute() mutates
        call.id to a tag number, which breaks the "current" resolution.
        """
        from nemo_oo_agents.runtime.context_vars import _get_agent_call_stack

        # Capture the call_id from the agent call stack BEFORE super().execute()
        # mutates call.id. Events are tagged with this value via the call stack,
        # so the EventQuery must match it explicitly.
        stack = _get_agent_call_stack()
        call_id = stack[-1] if stack else call.id

        # Swap the agent's render_config to use our plain formatter, threading
        # the pre-format char limit from TruncationConfig.
        original_render_config = runtime.agent.render_config
        tc = runtime.agent._truncation
        runtime.agent.render_config = original_render_config.model_copy(
            update={
                "provider_formatter": PlainProviderFormatter(
                    max_pre_format_chars=tc.max_pre_format_chars,
                )
            }
        )

        try:
            # Scope events to current call using the explicit call_id
            with ScopedContext(events=EventQuery(call_id=call_id)):
                return await super().execute(runtime, call)
        finally:
            # Restore original render config
            runtime.agent.render_config = original_render_config
