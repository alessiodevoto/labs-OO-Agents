# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Single long-lived ``prompt_toolkit.Application`` owning the whole TUI.

This is the "Plan C" rewrite: one Application that holds output
scrollback, the type-ahead queue region, the input buffer, and the
status line. No ``patch_stdout`` and no per-turn ``prompt_async`` —
so no handoff race that drops the first keystroke after the agent
finishes.

Grown incrementally against the failing tests in
``tests/cli/test_tui_app_behavior.py``. Each method exists because a
behaviour test needed it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.layout.processors import BeforeInput

from .queue_state import QueueState

# CSI + OSC stripper for the plain-text view of output_buffer. We keep
# the original ANSI in _output_ansi; tests that assert on buffer text
# don't want escape sequences in their comparisons.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# TODO(TUI-DIAG): remove once the plan-c TUI is stable. Writes
# timestamped breadcrumbs to /tmp/plan-c-trace.log so we can tell which
# code path fires (or doesn't) during the interactive session without
# clobbering the rendered layout.
def _diag(msg: str) -> None:
    import os as _os
    import time as _time

    try:
        with open("/tmp/plan-c-trace.log", "a", encoding="utf-8") as f:
            f.write(f"{_time.monotonic():.3f} {msg}\n")
    except Exception:
        pass
    # Best-effort — swallow file errors so diagnostics can't wedge the TUI.
    _ = _os  # retain import for future ad-hoc additions


PROMPT_MARKER = "❯ "

# Minimal default completions so Tab works on a bare TUIApplication.
# Production wiring replaces this with the real ``CommandRegistry``
# completer — the interface is just ``Completer.get_completions``.
_DEFAULT_COMPLETIONS = ["/help", "/exit", "/clear", "/compact", "!bash", "!ipython"]


class _PrefixCompleter(Completer):
    """Tiny completer: suggests entries from ``candidates`` when the buffer
    starts with ``/`` or ``!`` and is a prefix of a candidate."""

    def __init__(self, candidates: list[str]) -> None:
        self.candidates = candidates

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text or text[0] not in "/!":
            return
        for cand in self.candidates:
            if cand.startswith(text) and cand != text:
                yield Completion(cand, start_position=-len(text))


class TUIApplication:
    """Owns a single, long-lived ``prompt_toolkit.Application`` for the TUI."""

    def __init__(
        self,
        agent: Any = None,
        *,
        on_command: Callable[[str], Awaitable[None] | None] | None = None,
        on_bang: Callable[[str], Awaitable[None] | None] | None = None,
        on_user_message: Callable[[str], Awaitable[None] | None] | None = None,
        completer: Completer | None = None,
        session_label: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            agent: Object with an async ``respond(user_message)`` method.
            on_command: Called with the raw slash text (e.g. ``"/help"``)
                whenever the user submits one. Session wires this to its
                CommandRegistry. If omitted, commands still land in
                ``commands_dispatched()`` for introspection but nothing
                runs.
            on_bang: Called with the bang body (e.g. ``"echo hi"`` for
                ``!echo hi``). Session wires this to run_in_terminal +
                bash. If omitted, bang commands are only recorded in
                ``last_bang_command()``.
            completer: Optional prompt_toolkit ``Completer`` for Tab
                completion. When omitted a small built-in completer
                covers ``/help /exit /clear /compact !bash !ipython``.
        """
        self.agent = agent
        self._on_command = on_command
        self._on_bang = on_bang
        self._on_user_message = on_user_message
        self._session_label_fn: Callable[[], str] | None = session_label
        self.state = QueueState()

        # Output scrollback. Two parallel stores:
        #   * ``output_buffer`` — plain-text view, used by tests and by
        #     callers that want a printable transcript. ANSI stripped.
        #   * ``_output_ansi`` — raw ANSI chunks rendered into the live
        #     TUI via FormattedTextControl so Rich styling survives.
        self.output_buffer = Buffer(read_only=False)
        self._output_ansi: list[str] = []

        # Input window: where user keystrokes land. A caller (Session)
        # passes the real CommandRegistry-backed completer; otherwise
        # fall back to the minimal slash/bang stub.
        self._completer = completer or _PrefixCompleter(list(_DEFAULT_COMPLETIONS))
        self.input_buffer = Buffer(
            multiline=True,
            completer=self._completer,
            complete_while_typing=False,
            accept_handler=self._accept_handler,
        )

        # History — a plain list of submitted strings and a cursor that
        # tracks Up/Down navigation. Simpler than prompt_toolkit's async
        # InMemoryHistory machinery, which requires juggling working_lines
        # and _load_history_task to survive Buffer.reset().
        self._history: list[str] = []
        self._history_cursor: int | None = None

        # Command routing. Slash (/foo) items are appended to
        # ``_commands_dispatched``; bang (!foo) items set
        # ``_last_bang_command`` and (in production) run via
        # ``run_in_terminal``. Tests read both via the accessor methods.
        self._commands_dispatched: list[str] = []
        self._last_bang_command: str | None = None

        # Status line fields — surfaced via status_text().
        self._session_label: str = ""
        self._spinner_frame: str = "⠋"
        self._spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spinner_task: asyncio.Task | None = None

        self._prompt_processor = BeforeInput(PROMPT_MARKER, style="class:prompt")
        self._agent_task: asyncio.Task | None = None

        # Single producer-many-consumers path for transcript content:
        # emit_block() enqueues one ANSI chunk; a single background task
        # (started in run_async) drains the queue in order and writes
        # each chunk via run_in_terminal → sys.__stdout__. Everything
        # that used to have its own scheduling (patch_stdout proxy,
        # direct run_in_terminal in _render_message, etc.) now funnels
        # through this one queue — no races.
        self._block_queue: asyncio.Queue[str] | None = None
        self._consumer_task: asyncio.Task | None = None

        # Let the FakeAgent (or real agent) push output into our scrollback
        # without knowing about our internals.
        if hasattr(agent, "emit"):
            agent.emit = self.append_output  # type: ignore[attr-defined]

        kb = self._build_key_bindings()

        # Output scrollback lives in the terminal itself (above the
        # active region) via patch_stdout + sys.stdout writes — see
        # ``append_output``. No output Window in the layout: keeps the
        # active region tiny and preserves native terminal scrollback.

        # Queue window: shown only while state.messages / state.commands
        # are non-empty. Mirrors the pre-rewrite ``│ foo`` visual.
        def _queue_formatted():
            lines = []
            for msg in self.state.messages:
                for line in msg.split("\n"):
                    lines.append(("class:queue", f"│ {line}\n"))
            for cmd in self.state.commands:
                lines.append(("class:queue", f"│ {cmd}\n"))
            return lines

        queue_window = ConditionalContainer(
            Window(
                FormattedTextControl(_queue_formatted, focusable=False),
                dont_extend_height=True,
            ),
            filter=Condition(lambda: bool(self.state.messages or self.state.commands)),
        )

        input_window = Window(
            BufferControl(
                self.input_buffer,
                input_processors=[self._prompt_processor],
            ),
            wrap_lines=True,
            dont_extend_height=True,
        )
        self._input_window = input_window

        # Status line at the bottom — shows spinner + session label.
        def _status_formatted():
            text = self.status_text()
            return [("class:status", text)] if text else []

        status_window = Window(FormattedTextControl(_status_formatted, focusable=False), height=1)

        # Session rule: right above the input, always visible. Shows the
        # session name + short uuid + context-usage label, right-aligned
        # on a horizontal rule. Built from formatted text (not a Rich
        # Rule) so it re-measures with the live terminal width.
        def _session_rule_formatted():
            try:
                import shutil as _sh

                cols = max(_sh.get_terminal_size((120, 24)).columns, 20)
            except Exception:
                cols = 120
            label = self._session_label_fn() if self._session_label_fn is not None else ""
            if label:
                dashes = max(cols - len(label) - 1, 1)
                return [
                    ("class:rule", "─" * dashes + " "),
                    ("class:rule.label", label),
                ]
            return [("class:rule", "─" * cols)]

        session_rule = Window(
            FormattedTextControl(_session_rule_formatted, focusable=False), height=1
        )

        # Completion menu as a real layout region below the input.
        # Shrinks to the number of completions (with a 12-row cap) so the
        # HSplit doesn't inflate it with blank space when there are only
        # 1–4 matches. The stock ``CompletionsMenu`` wraps the control in
        # a Window with ``Dimension(min=1, max=12)`` and no preferred
        # size — HSplit then gives it the max height, leading to ugly
        # gaps below the completions when the list is short. We use
        # ``CompletionsMenuControl`` directly so we can set a dynamic
        # ``preferred`` height based on the actual completion count.
        _COMPLETION_MAX = 12

        def _completions_height() -> Dimension:
            state = self.input_buffer.complete_state
            n = len(state.completions) if state is not None else 0
            exact = min(max(n, 1), _COMPLETION_MAX)
            # Exact, not a range. Dimension(min=1, max=12) lets HSplit
            # inflate the window when extra space is available — which
            # is exactly what causes growing blank gaps between the
            # prompt and the menu as completions narrow.
            return Dimension(min=exact, max=exact, preferred=exact)

        completions_window = ConditionalContainer(
            Window(
                content=CompletionsMenuControl(),
                width=Dimension(min=8),
                height=_completions_height,
                dont_extend_height=True,
                right_margins=[ScrollbarMargin(display_arrows=True)],
            ),
            filter=Condition(lambda: self.input_buffer.complete_state is not None),
        )

        # Active region (top → bottom):
        #   queued type-ahead lines (only while agent working)
        #   status (spinner + optional badges)
        #   session rule — always visible, sits flush against the prompt
        #   input
        #   completions (only while completing)
        self._app = Application(
            layout=Layout(
                HSplit(
                    [
                        queue_window,
                        status_window,
                        session_rule,
                        input_window,
                        completions_window,
                    ],
                ),
                focused_element=input_window,
            ),
            key_bindings=kb,
            full_screen=False,
        )

    # ── key bindings --------------------------------------------------

    def _build_key_bindings(self):  # returns KeyBindingsBase (union of KB + merged)
        from .input_handler import create_key_bindings as _legacy_kb

        # The legacy bindings handle Enter (accept_handler dispatches to
        # our _accept_handler), Alt+Enter / Ctrl+J (newline), Tab (via
        # default bindings), and the slash/bang auto-trigger that re-opens
        # the completion menu as the user types a command.
        legacy = _legacy_kb(vi_mode=False)

        kb = KeyBindings()

        @kb.add("c-c")
        def _(event):
            # If an agent is running, C-c cancels the task and keeps the
            # buffer. Otherwise it exits the app.
            if self.is_thinking() and self._agent_task is not None:
                self._agent_task.cancel()
                return
            event.app.exit()

        @kb.add("c-d")
        def _(event):
            event.app.exit()

        @kb.add("tab")
        def _(event):
            # Explicit Tab trigger — opens the completion menu for the
            # current buffer. (``create_key_bindings`` auto-opens on /,
            # but an explicit Tab press is the standard UX.)
            event.current_buffer.start_completion(select_first=False)

        # Empty-buffer Up: queue pop wins over history — matches the
        # pre-rewrite typeahead UX (pop the last thing you typed while
        # the agent was working so you can edit it).
        empty_buffer = Condition(lambda: self.input_buffer.text == "")

        @kb.add("up", filter=empty_buffer)
        def _(event):
            popped = self.state.pop_last_for_edit()
            if popped is not None:
                self.input_buffer.text = popped
                self.input_buffer.cursor_position = len(popped)
                return
            self._history_navigate(-1)

        @kb.add("down", filter=empty_buffer)
        def _(event):
            self._history_navigate(+1)

        # Esc: soft-cancel the agent while preserving the queue. Any
        # messages already submitted during the turn are delivered as
        # the next respond() via the done-callback.
        @kb.add("escape")
        def _(event):
            if self.is_thinking() and self._agent_task is not None:
                self._agent_task.cancel()

        # Merge so our bindings (C-c with is_thinking awareness, Tab
        # trigger, Esc cancel, empty-buffer Up/Down for queue+history)
        # override the legacy bindings for the same keys, while legacy
        # still provides Enter → accept_handler, Alt+Enter newline, and
        # the slash auto-trigger characters.
        return merge_key_bindings([legacy, kb])

    # ── submission pipeline -------------------------------------------

    def _accept_handler(self, buffer: Buffer) -> bool:
        """prompt_toolkit accept_handler — invoked by ``validate_and_handle()``.

        When the agent is working, the submission is queued instead —
        messages collect in ``state.messages``, slash commands in
        ``state.commands``. Both are flushed when the agent finishes.

        Returning False tells prompt_toolkit to reset the buffer (clear
        the text, don't keep it as the working-lines tip).
        """
        text = buffer.text
        if not text.strip():
            return False
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_cursor = None

        if self.is_thinking():
            self.state.submit(text)
            return False

        if text.startswith("/"):
            self._commands_dispatched.append(text)
            self._fire(self._on_command, text)
            return False
        if text.startswith("!"):
            body = text[1:].strip()
            self._last_bang_command = body
            self._fire(self._on_bang, body)
            return False

        self._launch_agent(text)
        return False

    def _fire(self, cb: Callable[[str], Awaitable[None] | None] | None, arg: str) -> None:
        """Call a user callback; schedule coroutines on the loop.

        Surfaces any exception into the scrollback — without this an
        unhandled error in on_command / on_bang would disappear into
        asyncio's default handler and the user would see a no-op.
        """
        if cb is None:
            return
        try:
            result = cb(arg)
        except BaseException as exc:
            self.append_output(f"[error in callback] {type(exc).__name__}: {exc}\n")
            return
        if asyncio.iscoroutine(result):
            task = asyncio.ensure_future(result)

            def _report(t: asyncio.Task) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc is not None:
                    self.append_output(f"[error in callback] {type(exc).__name__}: {exc}\n")

            task.add_done_callback(_report)

    def _ensure_spinner_task(self) -> None:
        """Start a background task cycling the spinner frame while the
        agent is thinking. Invalidates the app each tick so the status
        line redraws; exits when ``is_thinking()`` becomes False."""
        if self._spinner_task is not None and not self._spinner_task.done():
            return

        async def _animate() -> None:
            i = 0
            try:
                while self.is_thinking():
                    self._spinner_frame = self._spinner_frames[i % len(self._spinner_frames)]
                    if self._app.is_running:
                        self._app.invalidate()
                    i += 1
                    await asyncio.sleep(0.08)
            finally:
                # Paint once after the agent stops so "thinking…" clears.
                if self._app.is_running:
                    self._app.invalidate()

        self._spinner_task = asyncio.ensure_future(_animate())

    def _history_navigate(self, direction: int) -> None:
        """Move the history cursor by ``direction`` (-1=older, +1=newer)."""
        if not self._history:
            return
        if self._history_cursor is None:
            if direction < 0:
                self._history_cursor = len(self._history) - 1
            else:
                return
        else:
            new = self._history_cursor + direction
            if new < 0 or new >= len(self._history):
                return
            self._history_cursor = new
        self.input_buffer.text = self._history[self._history_cursor]
        self.input_buffer.cursor_position = len(self.input_buffer.text)

    def _launch_agent(self, user_message: str) -> None:
        """Kick off ``agent.respond(user_message)`` on the event loop.

        Before invoking the agent we fire ``on_user_message`` so Session
        can render the user's input into the scrollback (the user wants
        to see what they typed, not just the agent's reply).
        """
        _diag(f"_launch_agent text={user_message[:60]!r}")
        self._fire(self._on_user_message, user_message)
        if self.agent is None:
            return
        coro = self.agent.respond(user_message)
        if not asyncio.iscoroutine(coro):
            return
        self._agent_task = asyncio.ensure_future(coro)
        self._agent_task.add_done_callback(self._on_agent_done)
        self._ensure_spinner_task()

    def _on_agent_done(self, task: asyncio.Task) -> None:
        """Fired once the agent's respond() returns / errors / is cancelled.

        Drains the type-ahead queue: any ``state.messages`` become the
        next agent turn (joined with blank lines); ``state.commands``
        move into ``_commands_dispatched``. If neither is present, we go
        idle — the buffer is already accepting input from the user.
        """
        _diag(
            f"_on_agent_done cancelled={task.cancelled()} "
            f"exc={None if task.cancelled() else task.exception()!r} "
            f"queue_msgs={len(self.state.messages)} "
            f"queue_cmds={len(self.state.commands)}"
        )
        # Surface errors into output scrollback. Cancellation is not an
        # error (Esc soft-cancel + Ctrl-C both cancel on purpose) but
        # still emit a visible ack so the user knows the interrupt
        # landed — the old TUI printed "Agent interrupted." here.
        if task.cancelled():
            self.append_output("\x1b[33m✗ Interrupted.\x1b[0m\n")
        else:
            exc = task.exception()
            if exc is not None:
                self.append_output(f"Agent error: {exc}")

        # Drain queued type-ahead items in submission order. Commands
        # fire via on_command (scheduled); consecutive message items
        # are joined into a single next-turn input.
        while self.state.items:
            kind, text = self.state.items[0]
            if kind == "cmd":
                self.state.items.pop(0)
                self._commands_dispatched.append(text)
                self._fire(self._on_command, text)
                continue
            # Collect all CONSECUTIVE messages — they were submitted
            # without a command interleaved, so deliver as one turn.
            msgs: list[str] = []
            while self.state.items and self.state.items[0][0] == "msg":
                msgs.append(self.state.items.pop(0)[1])
            self._launch_agent("\n\n".join(msgs))
            # Stop draining; the new agent task's on-done callback will
            # pick up where we left off.
            return

    # ── output pipeline -----------------------------------------------

    def emit_block(self, text: str) -> None:
        """Enqueue one ANSI-bearing block for the transcript.

        This is the ONE public contract for writing to the transcript:
        all producers (activity lines, code cells, agent markdown,
        interrupt notices, user echo) call this. A single consumer
        task drains the queue and writes each block in FIFO order via
        ``run_in_terminal`` → ``sys.__stdout__``. No races.

        Thread-safe: if called from a non-event-loop context, uses
        ``call_soon_threadsafe`` to enqueue.
        """
        if not text:
            return

        # Mirror the plain-text transcript for tests.
        self._output_ansi.append(text)
        stripped = _strip_ansi(text)
        existing = self.output_buffer.text
        joined = (
            existing + stripped
            if not existing or existing.endswith("\n")
            else existing + "\n" + stripped
        )
        self.output_buffer.document = Document(text=joined, cursor_position=len(joined))

        # Enqueue for the consumer. Before the consumer is up (pre
        # run_async), fall back to a plain stdout write.
        if self._block_queue is None:
            import sys as _sys

            try:
                _sys.stdout.write(text)
                _sys.stdout.flush()
            except Exception:
                pass
            return
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(self._block_queue.put_nowait, text)
        except RuntimeError:
            self._block_queue.put_nowait(text)

    # Legacy name — producers written before emit_block call this.
    append_output = emit_block

    # ── surface the harness (and real callers) rely on ----------------

    @property
    def is_running(self) -> bool:
        return self._app.is_running

    async def run_async(self) -> None:
        # Start the single-consumer block drainer. It lives for the
        # life of the app and is cancelled on teardown.
        self._block_queue = asyncio.Queue()
        self._consumer_task = asyncio.ensure_future(self._consume_blocks())
        try:
            await self._app.run_async()
        finally:
            if self._consumer_task is not None:
                self._consumer_task.cancel()
                try:
                    await self._consumer_task
                except (asyncio.CancelledError, BaseException):
                    pass
            self._consumer_task = None
            self._block_queue = None

    async def _consume_blocks(self) -> None:
        """Drain ``_block_queue`` forever; write each block above the
        prompt via ``run_in_terminal`` → ``sys.__stdout__``.

        One consumer, FIFO order, no races. Writing to ``__stdout__``
        (not ``sys.stdout``) bypasses the framework's ContextVarStream
        wrapper so ``self.message()`` content never gets captured as
        cell stdout.
        """
        import sys as _sys

        from prompt_toolkit.application import run_in_terminal

        assert self._block_queue is not None
        while True:
            text = await self._block_queue.get()

            def _write(t: str = text) -> None:
                out = _sys.__stdout__
                if out is not None:
                    out.write(t)
                    out.flush()

            try:
                await run_in_terminal(_write)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Best-effort — a single failed write shouldn't wedge
                # the consumer. Fall through and pick up the next block.
                pass

    def exit(self) -> None:
        if self._app.is_running:
            self._app.exit()

    def prompt_char_visible(self) -> bool:
        """True once the prompt-marker processor is attached to the input."""
        return self._prompt_processor is not None

    def input_cursor_position(self) -> int:
        """Current cursor position within the input buffer (0-indexed)."""
        return self.input_buffer.cursor_position

    def is_thinking(self) -> bool:
        """True while an agent task is live (respond() not yet returned)."""
        return self._agent_task is not None and not self._agent_task.done()

    def commands_dispatched(self) -> list[str]:
        """Slash commands the user has submitted, in order."""
        return list(self._commands_dispatched)

    def last_bang_command(self) -> str | None:
        """Most recent ``!shell-command`` the user submitted, or None."""
        return self._last_bang_command

    def completion_candidates(self) -> list[str]:
        """Completion candidates currently offered for the input buffer text.

        Returns each candidate as the *full* replacement string (i.e. what
        the buffer would contain if that candidate were applied) — so a
        Completion(text='/help', start_position=-3) against buffer '/he'
        reads back as '/help', not '/he/help'.
        """
        from prompt_toolkit.completion import CompleteEvent

        doc = self.input_buffer.document
        before = doc.text_before_cursor
        result = []
        for c in self._completer.get_completions(doc, CompleteEvent()):
            prefix = before[: c.start_position] if c.start_position < 0 else before
            result.append(prefix + c.text)
        return result

    def status_text(self) -> str:
        """One-line status area text.

        Shows ``<spinner> thinking...`` while the agent is working and a
        bracketed session label when one is set. Example::

            ⠋ thinking...    [session-abc]
        """
        parts: list[str] = []
        if self.is_thinking():
            parts.append(f"{self._spinner_frame} thinking...")
        if self._session_label:
            parts.append(f"[{self._session_label}]")
        return "   ".join(parts)

    def set_session_label(self, label: str) -> None:
        """Set the bracketed label shown on the right of the status line."""
        self._session_label = label

    def handle_resize(self, cols: int, rows: int) -> None:
        """Hint the app to re-layout for a new terminal size.

        prompt_toolkit handles real resize events internally; this hook
        exists for tests (and callers that want to force a redraw after
        a non-SIGWINCH layout change).
        """
        if self._app.is_running:
            self._app.invalidate()
