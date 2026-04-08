"""Streaming display for agent events.

Shows real-time updates for:
- Messages (assistant responses)
- Reasoning (chain-of-thought)
- Python code execution (with syntax highlighting)

Uses only event manager subscriptions (no hooks).
"""

import sys
import time

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from context_blocks import ResultStatus

from .theme import COLORS

try:
    from agent006.events import _NO_RETURN as _NO_RETURN_SENTINEL
except ImportError:
    _NO_RETURN_SENTINEL = None


class StreamingDisplay:
    """Display agent events in real-time with styled panels.

    Uses event manager subscriptions for all event types:
    - reasoning: Chain-of-thought (accumulated, displayed with execution)
    - llm_output: LLM response containing code
    - python_output: Execution output (stdout, stderr, value, error)
    - message: User-facing messages via message()

    Usage:
        display = StreamingDisplay(console, tui_console)
        display.attach(agent)  # Subscribe to agent events

        # Events are displayed automatically as they occur
        # Call display.detach() when done
    """

    def __init__(self, console: Console, tui_console=None):
        self.console = console
        self._tui_console = tui_console  # For stopping spinner
        self._unsubscribe_fns: list = []
        self.show_python: bool = True  # Toggle via /python on|off

        # Accumulated state for the current iteration
        self._pending_reasoning: list[str] = []
        self._pending_code: dict[str, str] = {}  # tool_call_id -> code
        self._messages: list[str] = []  # Messages sent via message()

    def attach(self, agent) -> None:
        """Subscribe to agent events.

        Args:
            agent: An Agent006 agent. All TUI agents have ``event_manager``
                (either TUIAgent or PassthroughAgent), so this always connects.
        """
        # Subscribe to reasoning events (accumulate, don't print immediately)
        unsub1 = agent.event_manager.on("reasoning", self._on_reasoning)
        self._unsubscribe_fns.append(unsub1)

        # Subscribe to message events (user-facing messages via message())
        unsub2 = agent.event_manager.on("message", self._on_message)
        self._unsubscribe_fns.append(unsub2)

        # Subscribe to tool_call events to capture code
        unsub3 = agent.event_manager.on("tool_call", self._on_tool_call)
        self._unsubscribe_fns.append(unsub3)

        # Subscribe to python_output events (contains the output)
        unsub4 = agent.event_manager.on("python_output", self._on_python_output)
        self._unsubscribe_fns.append(unsub4)

    def detach(self) -> None:
        """Unsubscribe from all events."""
        for unsub in self._unsubscribe_fns:
            try:
                unsub()
            except Exception:
                pass
        self._unsubscribe_fns.clear()

    def clear(self) -> None:
        """Clear all pending state."""
        self._pending_reasoning.clear()
        self._pending_code.clear()
        self._messages.clear()

    def consume_messages(self) -> list[str]:
        """Return messages collected during this response and clear the buffer."""
        msgs, self._messages = self._messages, []
        return msgs

    def _on_reasoning(self, event) -> None:
        """Accumulate reasoning events (displayed with next execution)."""
        content = event.content or ""
        if content.strip():
            self._pending_reasoning.append(content)

    def _on_message(self, event) -> None:
        """Capture message events for display in Agent006 panel."""
        content = event.content or ""
        if content.strip():
            self._messages.append(content)

    def _on_tool_call(self, event) -> None:
        """Capture code from execute_python tool calls."""
        # Only capture execute_python tool calls
        if getattr(event, "name", "") == "execute_python":
            tool_call_id = getattr(event, "tool_call_id", "")
            arguments = getattr(event, "arguments", {})
            if tool_call_id and isinstance(arguments, dict):
                code = arguments.get("code", "")
                if code:
                    self._pending_code[tool_call_id] = code

    def _on_python_output(self, event) -> None:
        """Display combined panel with reasoning + code + output."""
        if not self.show_python:
            # Drain all pending state so it doesn't bleed into the next call
            self._pending_reasoning.clear()
            self._pending_code.clear()
            return

        # Stop spinner before printing (if TUI console provided)
        if self._tui_console is not None:
            self._tui_console.stop_spinner()
            # Small delay to ensure Live context is fully stopped
            time.sleep(0.01)

        elements = []

        # Add accumulated reasoning section
        if self._pending_reasoning:
            reasoning_text = "\n".join(self._pending_reasoning)
            elements.append(Text(reasoning_text, style=f"italic {COLORS['subtext1']}"))
            elements.append(Text(""))  # Spacing
            elements.append(Rule(style=COLORS["surface1"]))
            elements.append(Text(""))  # Spacing
            # Clear pending reasoning after displaying
            self._pending_reasoning.clear()

        # Add code section with syntax highlighting
        # Look up the code captured from the tool_call event
        code = self._pending_code.pop(event.tool_call_id, None)
        if code:
            code_syntax = Syntax(
                code.strip(),
                "python",
                theme="monokai",
                line_numbers=True,
                word_wrap=True,
            )
            elements.append(code_syntax)

        # Build output section from execution event
        output_parts = []

        if event.stdout:
            output_parts.append(Text(event.stdout, style=COLORS["text"]))

        if event.stderr:
            output_parts.append(Text(event.stderr, style=COLORS["peach"]))

        if event.error:
            output_parts.append(Text(event.error, style=f"bold {COLORS['red']}"))

        # Handle return value (if present and not an error)
        if event.execution_status == ResultStatus.COMPLETE and event.value is not None:
            if _NO_RETURN_SENTINEL is None or event.value is not _NO_RETURN_SENTINEL:
                val = event.value if isinstance(event.value, str) else repr(event.value)
                output_parts.append(Text(f"=> {val}", style=f"bold {COLORS['green']}"))

        # Add output section if any
        if output_parts:
            elements.append(Text(""))  # Spacing
            elements.append(Rule(style=COLORS["surface1"]))
            for part in output_parts:
                elements.append(part)

        # Only print if we have something to show
        if elements:
            panel = Panel(
                Group(*elements),
                title="[python]Python[/python]",
                border_style=COLORS["blue"],
                padding=(0, 1),
            )
            self.console.print(panel)

            # Force flush to ensure output appears immediately (not buffered)
            sys.stdout.flush()
            sys.stderr.flush()

        # Restart spinner — agent may still be running between tool calls
        if self._tui_console is not None:
            self._tui_console.start_spinner()
