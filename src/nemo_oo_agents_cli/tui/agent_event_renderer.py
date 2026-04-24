# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Per-turn rendering for agent events into the TUI's block queue.

``AgentEventRenderer`` subscribes to the agent's ``Reasoning``,
``ToolCallEvent`` and ``PythonOutput`` events and forwards them to
``emit_text`` as Rich renderables. It also owns the monkey-patched
``agent._render_message`` hook so ``self.message()`` output lands in
the transcript in the right order relative to code cells.

State (all per-turn):

- ``_agent_has_messaged`` — True after the first ``self.message()`` in
  the current turn. Drives the one-time ``"OO ─"`` rule that separates
  agent output from user input.
- ``_pending_messages`` — buffer for ``self.message()`` calls.
  Unconditionally buffered; drained on every ``PythonOutput`` so
  messages always land BELOW the code-preview + output block for the
  cell above them.

The class has no reference to ``Session``. Callers pass ``emit_text``
(the ANSI-enqueue function), a ``show_python`` getter, and a shared
``pending_code`` dict (keyed by ``tool_call_id``) that pairs a code
preview with its eventual output.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from rich.markdown import Markdown
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from .code_preview import _code_preview


class _ReasoningEvent(Protocol):
    """Fields the renderer reads from a ``Reasoning`` event."""

    content: str


class _ToolCallEvent(Protocol):
    """Fields the renderer reads from a ``ToolCallEvent``."""

    name: str
    tool_call_id: str
    arguments: dict[str, Any]


class _PythonOutputEvent(Protocol):
    """Fields the renderer reads from a ``PythonOutput`` event."""

    tool_call_id: str
    stdout: str
    stderr: str


class _SummaryEvent(Protocol):
    """Fields the renderer reads from a ``Summary`` event."""

    summary_tag: str
    replaced_range: tuple[int, int]
    children_tags: list[str]
    summary_text: str | None


class AgentEventRenderer:
    """Render agent events into the TUI block queue, in turn order."""

    def __init__(
        self,
        *,
        agent: Any,
        emit_text: Callable[[Any], None],
        show_python: Callable[[], bool],
        pending_code: dict[str, str],
        colors: dict[str, str],
    ) -> None:
        self._agent = agent
        self._emit_text = emit_text
        self._show_python = show_python
        self._pending_code = pending_code
        self._colors = colors
        self._unsubscribes: list[Callable[[], None]] = []
        self._agent_has_messaged = False
        self._pending_messages: list[str] = []
        # Assigned by attach() with whatever was on agent._render_message
        # before; detach() restores to this value. Declaring it here
        # means detach() can read it without a defensive getattr and
        # pyright sees the field.
        self._prior_render_message: Callable[[str], None] | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def attach(self) -> None:
        """Subscribe to the agent's event manager and wire _render_message.

        Idempotent: a second call is a no-op (handlers already subscribed).
        Chains onto any existing ``agent._render_message`` so an observer
        hook installed by another component (e.g. a web mirror) still
        fires when ``self.message()`` is called.
        """
        if self._unsubscribes:
            return
        em = self._agent.event_manager
        self._unsubscribes.extend(
            [
                em.on("Reasoning", self._on_reasoning),
                em.on("ToolCallEvent", self._on_tool_call),
                em.on("PythonOutput", self._on_python_output),
                em.on("Summary", self._on_summary),
            ]
        )
        # Chain onto any prior hook so we don't silently stomp observers.
        prior = getattr(self._agent, "_render_message", None)
        self._prior_render_message = prior
        if hasattr(self._agent, "_render_message"):

            def _hook(text: str) -> None:
                if prior is not None:
                    try:
                        prior(text)
                    except Exception:
                        pass
                self._render_message(text)

            self._agent._render_message = _hook

    def detach(self) -> None:
        """Unsubscribe all handlers and clear the agent hook.

        Safe to call multiple times. Restores ``agent._render_message``
        to whatever was there before ``attach()`` (usually ``None``) so
        a post-shutdown ``agent.message()`` doesn't invoke a dead renderer.
        """
        for fn in self._unsubscribes:
            try:
                fn()
            except Exception:
                pass
        self._unsubscribes.clear()
        # Restore the prior hook if the agent still has our chain installed.
        if hasattr(self._agent, "_render_message"):
            self._agent._render_message = self._prior_render_message

    def reset_turn(self) -> None:
        """Called on new user submission — flush any stragglers from the
        previous turn and reset the per-turn ``OO ─`` guard.

        Messages emitted after the last ``PythonOutput`` of a turn stay
        in the buffer (there's no "turn ended" event). Flushing on the
        next user submission ensures they're never silently dropped —
        they appear just above the new user bar rather than being lost
        forever.
        """
        if self._pending_messages:
            self._flush_messages()
        self._agent_has_messaged = False

    # ── message rendering (agent self.message()) ───────────────────────

    def _render_message(self, text: str) -> None:
        """Hook plugged into ``agent._render_message``. Buffers
        unconditionally so every message lands AFTER the nearest
        preceding code block's ``PythonOutput`` flush — matching the
        visual contract "messages always below the anchor above them".

        A CodeAct prefill runs at the start of every turn, so the
        buffer drains quickly; messages don't sit visibly queued in
        normal flow.
        """
        self._pending_messages.append(str(text))

    def _emit_markdown(self, text: str) -> None:
        if not self._agent_has_messaged:
            self._agent_has_messaged = True
            self._emit_text(
                Rule(Text("OO ", style=self._colors["mauve"]), style="dim", align="left")
            )
        self._emit_text(Markdown(str(text)))

    def _flush_messages(self) -> None:
        """Drain ``_pending_messages`` in order, each as a markdown block."""
        while self._pending_messages:
            self._emit_markdown(self._pending_messages.pop(0))

    # ── agent event handlers ───────────────────────────────────────────

    def _on_reasoning(self, event: _ReasoningEvent) -> None:
        content = getattr(event, "content", "") or ""
        if content.strip():
            self._emit_text(Text(content, style="dim italic"))

    def _on_tool_call(self, event: _ToolCallEvent) -> None:
        name = getattr(event, "name", "")
        if name == "return_result":
            # ``return_result`` invoked as its own tool call (not inline
            # inside ``execute_python``) emits no PythonOutput, so
            # ``_on_python_output`` never fires the turn-end flush.
            # Any buffered ``self.message()`` calls would otherwise stay
            # stuck in ``_pending_messages`` past the agent's stop.
            self._flush_messages()
            return
        if name != "execute_python":
            return
        tool_call_id = getattr(event, "tool_call_id", "")
        arguments = getattr(event, "arguments", {})
        code = arguments.get("code", "") if isinstance(arguments, dict) else ""
        if not code:
            return
        self._pending_code[tool_call_id] = code

        # show_python=True mode renders the full cell from _on_python_output;
        # no teaser preview line needed.
        if self._show_python():
            return

        preview = (
            "Inspecting inputs..." if tool_call_id.startswith("prefill_") else _code_preview(code)
        )
        if not preview:
            return
        first_line = preview.split("\n", 1)[0]
        styled = Text(f"∴ {first_line}", style="dim")
        if first_line.lstrip().startswith("#"):
            styled.stylize("not dim", 0, len(first_line) + 2)
        if "\n" in preview:
            styled.append("\n  " + preview.split("\n", 1)[1], style="dim")
        self._emit_text(styled)

    def _on_python_output(self, event: _PythonOutputEvent) -> None:
        tool_call_id = getattr(event, "tool_call_id", "")
        code = self._pending_code.pop(tool_call_id, None)
        if tool_call_id.startswith("prefill_"):
            # Still flush any pending messages so they don't land below
            # a LATER cell's output.
            self._flush_messages()
            return

        stdout = str(getattr(event, "stdout", "") or "")
        stderr = str(getattr(event, "stderr", "") or "")

        # show_python=True: render a notebook-style cell — 'oo python'
        # rule, syntax-highlighted code, 'oo stdout' rule + stdout,
        # 'oo stderr' rule + stderr.
        if self._show_python() and code:
            mauve = self._colors["mauve"]
            red = self._colors["red"]
            text_color = self._colors["text"]
            self._emit_text(Rule(Text("oo python", style=mauve), style="dim", align="left"))
            self._emit_text(
                Syntax(code.strip(), "python", theme="monokai", background_color="default")
            )
            if stdout.strip():
                self._emit_text(Rule(Text("oo stdout", style=mauve), style="dim", align="left"))
                self._emit_text(Text(stdout.rstrip("\n"), style=text_color))
            if stderr.strip():
                self._emit_text(Rule(Text("oo stderr", style=red), style="dim", align="left"))
                self._emit_text(Text(stderr.rstrip("\n"), style=red))
            self._flush_messages()
            return

        # Preview mode: stdout is for the agent; user sees results via
        # self.message(). Only show stderr so errors aren't silent. Then
        # flush any self.message() calls so they appear BELOW the code
        # preview.
        if stderr.strip():
            for line in stderr.rstrip("\n").split("\n"):
                self._emit_text(Text(f"  │ {line}", style="red"))
        self._flush_messages()

    def _on_summary(self, event: _SummaryEvent) -> None:
        """Render a dim one-line notice when a summary/truncation is applied.

        Makes the summarizer visible: seeing back-to-back lines here is the
        signal that lets the user catch cascade bugs (same diagnosis we just
        did via the DB, but live). Format:

            ∴ summarized 1..600 · 600 events → 2.5KB summary
            ∴ truncated 1..600 · 600 events (no summary)
        """
        tag = getattr(event, "summary_tag", "") or ""
        rng = getattr(event, "replaced_range", None) or (0, 0)
        children = getattr(event, "children_tags", None) or []
        text = getattr(event, "summary_text", None)

        # Event count: prefer children_tags len (exact, survives nested
        # summaries) over replaced_range width (always an upper bound).
        n_events = len(children) if children else max(0, rng[1] - rng[0] + 1)

        if text is None:
            detail = f"{n_events} events (no summary)"
            verb = "truncated"
        else:
            chars = len(text)
            if chars >= 1024:
                size = f"{chars / 1024:.1f}KB"
            else:
                size = f"{chars}B"
            detail = f"{n_events} events → {size} summary"
            verb = "summarized"

        self._emit_text(Text(f"∴ {verb} {tag} · {detail}", style="dim italic"))
