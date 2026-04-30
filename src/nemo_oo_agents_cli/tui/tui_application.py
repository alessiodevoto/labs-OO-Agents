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
import logging
import re
import shutil

logger = logging.getLogger(__name__)
from collections.abc import Awaitable, Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
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


def terminal_cols(default: int = 120, minimum: int = 20) -> int:
    """Live terminal column count, clamped to [``minimum``, ∞).

    Wrapped so every caller gets the same fallback behaviour
    (``(120, 24)`` on stat failure) and clamp. Used by the status-rule
    renderer here and by the block-rendering helpers in ``Session`` so
    rich text (user-message bars, full-width rules) spans the live
    width and doesn't hardcode 120.
    """
    try:
        return max(shutil.get_terminal_size((default, 24)).columns, minimum)
    except Exception:
        return default


PROMPT_MARKER = "❯ "


def _coalesce_string_into_queue(inq: Any, text: str) -> None:
    """Push *text* onto *inq*, merging into the trailing item if it's a string.

    UX policy, lifted out of ``submit_message``: when a user types
    multiple lines in quick succession (Enter, type more, Enter), we
    want one composite multi-line item — not N tiny items the agent
    handles one-by-one. The trailing queued item is the merge target
    only if it's a ``str``; non-string items (anything a producer puts
    that isn't a typed message) are preserved unchanged.
    """
    tail = inq.pop_last()
    if isinstance(tail, str):
        inq.put(f"{tail}\n{text}")
        return
    if tail is not None:
        inq.put(tail)
    inq.put(text)


class TUIApplication:
    """Owns a single, long-lived ``prompt_toolkit.Application`` for the TUI."""

    def __init__(
        self,
        agent: Any = None,
        *,
        on_command: Callable[[str], Awaitable[None] | None] | None = None,
        on_bang: Callable[[str], Awaitable[None] | None] | None = None,
        completer: Completer | None = None,
        session_label: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            agent: Object with an async ``handle()`` method that pumps
                input queues (see ``BaseTUIAgent.handle``).
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
                completion. When omitted no completion is offered.

        The per-message echo ("queued → accepted" transition, user-bar
        render, TUIUserInput log) is wired on the agent's
        ``_user_messages_in`` Channel via ``set_on_get``. The
        dispatcher itself doesn't call back — that would double-fire
        the echo when the agent dequeues a message mid-turn.
        """
        self.agent = agent
        self._on_command = on_command
        self._on_bang = on_bang
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
        # Tab produces no suggestions.
        from prompt_toolkit.completion import DummyCompleter

        self._completer = completer or DummyCompleter()
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
        # Set by _run_callback on the sync-error path; read by
        # _drain_next to bail out of a pathological "every queued
        # command raises" loop instead of dumping N stack traces.
        self._last_sync_callback_raised: bool = False

        # Status line fields — surfaced via status_text().
        self._session_label: str = ""
        self._spinner_frame: str = "⠋"
        self._spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spinner_task: asyncio.Task | None = None

        self._prompt_processor = BeforeInput(PROMPT_MARKER, style="class:prompt")
        self._agent_task: asyncio.Task | None = None
        # True while the dispatcher is inside an ``agent.handle()`` call.
        # ``is_thinking()`` checks this flag rather than the user_messages
        # queue's waiter count — counting waiters conflated "dispatcher
        # idle between turns" with "agent mid-turn awaiting clarification
        # via await self.user_messages.get()". The latter case had the
        # spinner stop while the agent was genuinely working.
        self._in_respond: bool = False

        # Single producer-many-consumers path for transcript content:
        # emit_block() enqueues one ANSI chunk; a single background task
        # (started in run_async) drains the queue in order and writes
        # each chunk via run_in_terminal → sys.__stdout__. Everything
        # that used to have its own scheduling (patch_stdout proxy,
        # direct run_in_terminal in _render_message, etc.) now funnels
        # through this one queue — no races.
        self._block_queue: asyncio.Queue[str] | None = None
        self._consumer_task: asyncio.Task | None = None
        # Captured in run_async; used by emit_block for thread-safe
        # enqueue without calling the deprecated asyncio.get_event_loop().
        self._loop: asyncio.AbstractEventLoop | None = None
        # Set by run_async's stdout/stderr forwarder install; called in
        # the finally to restore the real streams.
        self._uninstall_stream_capture: Callable[[], None] | None = None

        # Let the FakeAgent (or real agent) push output into our scrollback
        # without knowing about our internals.
        if hasattr(agent, "emit"):
            agent.emit = self.emit_block  # type: ignore[attr-defined]

        kb = self._build_key_bindings()

        # Output scrollback lives in the terminal itself above the
        # active region: every ``emit_block`` enqueues a chunk that the
        # consumer task writes via ``run_in_terminal`` → ``sys.__stdout__``.
        # No output Window in the layout — keeps the active region tiny
        # and preserves native terminal scrollback.

        # Queue window: shown whenever the agent has unconsumed messages
        # in its user_messages input queue. Mirrors the pre-rewrite
        # ``│ foo`` visual. Reads the hidden Channel
        # (``_user_messages_in``) — the public ``user_messages`` is the
        # LLM-facing OutputQueue facade and doesn't expose snapshot.
        def _queue_pending() -> list[str]:
            if self.agent is None:
                return []
            q = getattr(self.agent, "_user_messages_in", None)
            if q is None:
                return []
            return q.snapshot()

        def _queue_formatted():
            lines = []
            for text in _queue_pending():
                for line in str(text).split("\n"):
                    lines.append(("class:queue", f"│ {line}\n"))
            return lines

        queue_window = ConditionalContainer(
            Window(
                FormattedTextControl(_queue_formatted, focusable=False),
                dont_extend_height=True,
            ),
            filter=Condition(lambda: bool(_queue_pending())),
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
            cols = terminal_cols(minimum=20)
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
            # When the Application exits (e.g. /exit), erase the live
            # region so the final screen is just the committed
            # scrollback. Otherwise the empty ❯ from the input line
            # gets a final redraw right before exit and appears as a
            # ghost prompt above '❯ /exit' in the transcript.
            erase_when_done=True,
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
            # Standard Tab: open the menu if closed, advance to the
            # next option if already open. start_completion doesn't
            # advance on repeat presses — complete_next does both.
            buf = event.current_buffer
            if buf.complete_state is None:
                buf.start_completion(select_first=True)
            else:
                buf.complete_next()

        @kb.add("s-tab")
        def _(event):
            buf = event.current_buffer
            if buf.complete_state is not None:
                buf.complete_previous()

        # Empty-buffer Up: queue pop wins over history — matches the
        # pre-rewrite typeahead UX (pop the last thing you typed while
        # the agent was working so you can edit it). In the forever-loop
        # model we pop from the agent's user_messages queue; items
        # already consumed by the agent can't be edited.
        empty_buffer = Condition(lambda: self.input_buffer.text == "")

        def _pop_last_queued() -> str | None:
            if self.agent is None:
                return None
            q = getattr(self.agent, "_user_messages_in", None)
            if q is None:
                return None
            item = q.pop_last()
            return None if item is None else str(item)

        @kb.add("up", filter=empty_buffer)
        def _(event):
            popped = _pop_last_queued()
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

        Slash/bang commands dispatch immediately. Plain text is pushed
        onto ``agent.user_messages`` via ``submit_message`` — the agent's
        forever-loop ``handle()`` picks it up when it calls
        ``self.get_next_input(...)``.

        Returning False tells prompt_toolkit to reset the buffer (clear
        the text, don't keep it as the working-lines tip).
        """
        text = buffer.text
        if not text.strip():
            return False
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_cursor = None

        if text.startswith("/"):
            self._commands_dispatched.append(text)
            self._run_callback(self._on_command, text)
            return False
        if text.startswith("!"):
            body = text[1:].strip()
            self._last_bang_command = body
            self._run_callback(self._on_bang, body)
            return False

        self.submit_message(text)
        return False

    def _run_callback(
        self,
        cb: Callable[[str], Awaitable[None] | None] | None,
        arg: str,
    ) -> asyncio.Task | None:
        """Invoke one user callback; return the scheduled Task or None.

        Used by every "call an out-of-band function from the TUI" site
        (``on_command``, ``on_bang``).

        - Synchronous callback (``None``, a regular function, or one
          that raised): returns ``None``. Errors are surfaced into the
          scrollback so an unhandled exception doesn't vanish into
          asyncio's default handler. Sets
          ``self._last_sync_callback_raised = True`` so callers that
          loop (``_drain_next``) can stop after a failure.
        - Coroutine callback: scheduled as a Task and returned. The
          caller can ``add_done_callback`` on it to chain follow-up
          work (e.g. ``_drain_next`` for queued commands). Errors
          inside the coroutine are surfaced via a done-callback
          installed here.
        """
        self._last_sync_callback_raised = False
        if cb is None:
            return None
        try:
            result = cb(arg)
        except BaseException as exc:
            self.emit_block(f"[callback error] {type(exc).__name__}: {exc}\n")
            self._last_sync_callback_raised = True
            return None
        if not asyncio.iscoroutine(result):
            return None
        task = asyncio.ensure_future(result)

        def _report(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                self.emit_block(f"[callback error] {type(exc).__name__}: {exc}\n")

        task.add_done_callback(_report)
        return task

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

    def submit_message(self, user_message: str) -> None:
        """Treat ``user_message`` as if the user just typed and submitted it.

        Pushes the text onto the agent's user_messages Channel and
        lazy-starts the dispatcher task. The user-bar echo + session
        bookkeeping fire later — on the Channel's ``on_get`` hook,
        when the message is actually dequeued — so a message typed
        while the agent is working only shows in the queue pane until
        its turn comes up. The hook fires identically whether the
        dispatcher or agent code pulls the item.

        Consecutive submissions that land while the dispatcher is
        still busy are *merged* into the trailing queue item with a
        ``\\n`` separator. That preserves the old "type, Enter, type,
        Enter → one message" UX where the user is composing a
        multi-line thought across several Enters.

        Public so ``Session`` can submit a message programmatically —
        e.g. a slash command that returns an ``agent_message``
        (``/compact``) and wants to drive the same path as a typed
        message.
        """
        if self.agent is None:
            return
        inq = getattr(self.agent, "_user_messages_in", None)
        if inq is None:
            self.emit_block("[submit_message dropped] agent has no user_messages queue\n")
            return
        _coalesce_string_into_queue(inq, user_message)
        self._ensure_dispatcher_task()

    def _ensure_dispatcher_task(self) -> None:
        """Start the per-turn dispatcher if not already live.

        The dispatcher is the outer loop around ``agent.handle()``:
        it reads the result's ``kind`` and waits on the appropriate
        queue before calling ``handle()`` again with the new
        ``(queue_name, item)`` notification.

        Lazy-started: on session load there's no task. The first user
        message triggers submit_message → put onto user_messages →
        _ensure_dispatcher_task → dispatcher loops forever until STOP.
        """
        if self.agent is None:
            return
        if self._agent_task is not None and not self._agent_task.done():
            return
        self._agent_task = asyncio.ensure_future(self._dispatcher_loop())
        self._agent_task.add_done_callback(self._on_agent_done)
        self._ensure_spinner_task()

    async def _dispatcher_loop(self) -> None:
        """Drive ``agent.handle()`` turn-by-turn until ``STOP``.

        The "queued → accepted" echo (user-bar render, TUIUserInput
        log) is fired by the user_messages channel's ``on_get`` hook —
        installed by ``Session`` — not from this dispatcher loop. That
        way the echo is symmetric: it fires both when the dispatcher
        takes the next user_messages item AND when the agent dequeues
        one mid-turn via ``await self.user_messages.get()``. Calling
        the hook here would double-render the dispatcher case.

        ``QueueManager.race()`` returns ``list[(name, item)]`` —
        currently always length 1, but the list shape is the contract
        so future ``deliver=`` modes can return more items per call
        without changing the dispatcher's loop body.
        """
        from nemo_oo_agents.runtime.channels import Channel

        agent = self.agent
        assert agent is not None

        user_messages_in: Channel = agent._user_messages_in
        qm = agent.queue_manager

        # Wait for the first user message (already queued by submit_message
        # that started us). qsize()>0 → get() returns immediately.
        queue_name = "user_messages"
        item = await user_messages_in.get()
        self._on_dispatcher_dequeued()

        while True:
            self._in_respond = True
            try:
                result = await agent.handle((queue_name, item))
            finally:
                self._in_respond = False

            kind = result.kind
            logger.info("[DISPATCHER] handle() returned kind=%r", kind)

            if kind == "STOP":
                return
            if kind == "WAIT":
                # race() raises ValueError if no queue-mode channels
                # are registered. Map that to the same "exit cleanly"
                # behaviour the previous wait_for_any path had.
                try:
                    items = await qm.race()
                except ValueError:
                    return
                queue_name, item = items[0]
            else:  # GET_USER_INPUT
                queue_name = "user_messages"
                item = await user_messages_in.get()
            self._on_dispatcher_dequeued()

    def _on_dispatcher_dequeued(self) -> None:
        """React to a just-dequeued item: redraw queue pane, restart spinner.

        Without this, the queue pane can show stale contents until the
        next event happens to trigger a redraw (spinner tick, user key,
        scrollback write). And the spinner animation task exits when
        ``is_thinking()`` was False between turns — a new turn wants
        it running again.
        """
        if self._app.is_running:
            self._app.invalidate()
        self._ensure_spinner_task()

    def _on_agent_done(self, task: asyncio.Task) -> None:
        """Fired when the dispatcher exits (STOP, error, or cancellation).

        On cancellation OR exception with messages still pending we
        lazy-restart so neither soft-cancel (Esc) nor a crashed turn
        strands queued input — the user can keep typing and the next
        message wakes the dispatcher again. STOP exits cleanly.
        """
        if task.cancelled():
            self.emit_block("\x1b[33m✗ Interrupted.\x1b[0m\n")
            q = getattr(self.agent, "_user_messages_in", None) if self.agent else None
            if q is not None and q.qsize() > 0:
                self._ensure_dispatcher_task()
            return
        exc = task.exception()
        if exc is not None:
            self.emit_block(f"Agent error: {exc}\n")
            q = getattr(self.agent, "_user_messages_in", None) if self.agent else None
            if q is not None and q.qsize() > 0:
                self._ensure_dispatcher_task()

    # ── output pipeline -----------------------------------------------

    def emit_block(self, text: str) -> None:
        """Enqueue one ANSI-bearing block for the transcript.

        This is the ONE public contract for writing to the transcript:
        all producers (activity lines, code cells, agent markdown,
        interrupt notices, user echo) call this. A single consumer
        task drains the queue and writes each block in FIFO order via
        ``run_in_terminal`` → ``sys.__stdout__``. No races.

        Thread-safe: any mutation of prompt_toolkit state (``Buffer``
        document, which fires callbacks synchronously) is routed through
        ``call_soon_threadsafe`` so it always runs on the loop thread.
        ``list.append`` on ``_output_ansi`` is already GIL-safe.
        """
        if not text:
            return

        # GIL-safe: list.append is atomic. Reading from off-thread is fine.
        self._output_ansi.append(text)

        # Before the consumer is up (pre-run_async) we're single-threaded
        # by construction — safe to touch the buffer directly + emit to
        # stdout. After run_async, route everything via the loop.
        if self._block_queue is None or self._loop is None:
            self._append_stripped_to_buffer(text)
            import sys as _sys

            try:
                _sys.stdout.write(text)
                _sys.stdout.flush()
            except Exception:
                pass
            return

        # On-thread fast path: mutate buffer directly so tests that
        # inspect ``output_buffer.text`` right after a call see the
        # update without waiting for a loop tick.
        try:
            on_thread = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_thread = False
        if on_thread:
            self._append_stripped_to_buffer(text)
            self._block_queue.put_nowait(text)
            return

        # Off-thread: route everything through the loop so off-thread
        # producers (e.g. IPython worker, OTEL exporter) never touch
        # prompt_toolkit state directly.
        self._loop.call_soon_threadsafe(self._append_stripped_to_buffer, text)
        self._loop.call_soon_threadsafe(self._block_queue.put_nowait, text)

    def _append_stripped_to_buffer(self, text: str) -> None:
        """Append the ANSI-stripped transcript text to ``output_buffer``.

        Runs on the event loop thread (either because ``emit_block``
        scheduled it via ``call_soon_threadsafe`` or because we're still
        in the pre-consumer, single-threaded bootstrap phase).
        """
        stripped = _strip_ansi(text)
        existing = self.output_buffer.text
        joined = (
            existing + stripped
            if not existing or existing.endswith("\n")
            else existing + "\n" + stripped
        )
        self.output_buffer.document = Document(text=joined, cursor_position=len(joined))

    # ── surface the harness (and real callers) rely on ----------------

    @property
    def is_running(self) -> bool:
        return self._app.is_running

    async def run_async(self) -> None:
        # Capture the loop once so emit_block can enqueue safely from
        # any thread without calling the deprecated get_event_loop().
        self._loop = asyncio.get_running_loop()
        self._block_queue = asyncio.Queue()
        self._consumer_task = asyncio.ensure_future(self._consume_blocks())

        # Route stray sys.stdout / sys.stderr writes (aiohttp warnings,
        # litellm noise, stray prints) into the scrollback instead of
        # letting them corrupt prompt_toolkit's paint. Must install here
        # — before the first agent cell runs and before the framework
        # wraps sys.stdout with ContextVarStream — so agent-cell stdout
        # capture layers on top and still works unchanged.
        from .stream_forwarder import install_stray_stream_capture

        self._uninstall_stream_capture = install_stray_stream_capture(self.emit_block)
        try:
            # set_exception_handler=False keeps the handler Session installed
            # (_loud_handler) active for the whole app lifetime. Otherwise
            # prompt_toolkit replaces it with its own, which prints "Exception
            # None\nPress ENTER to continue..." for non-exception asyncio
            # contexts (e.g. "Task was destroyed but it is pending!") and
            # swallows every other diagnostic field.
            await self._app.run_async(set_exception_handler=False)
        finally:
            # Restore sys.stdout / sys.stderr FIRST so any post-exit
            # prints from teardown code (spinner cleanup, snapshot save,
            # goodbye message) go straight to the real terminal rather
            # than back into the dying block queue.
            uninstall = getattr(self, "_uninstall_stream_capture", None)
            if uninstall is not None:
                try:
                    uninstall()
                except Exception:
                    pass
                self._uninstall_stream_capture = None

            # Drain any blocks queued during teardown (e.g. 'Goodbye!
            # Stay vibing.' from /exit) straight to the real stdout —
            # the consumer task's run_in_terminal no longer works once
            # the app has exited.
            import sys as _sys

            q = self._block_queue
            if q is not None:
                while not q.empty():
                    try:
                        chunk = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    out = _sys.__stdout__
                    if out is not None:
                        try:
                            out.write(chunk)
                            out.flush()
                        except Exception:
                            pass
            if self._consumer_task is not None:
                self._consumer_task.cancel()
                try:
                    await self._consumer_task
                except asyncio.CancelledError:
                    pass
                except BaseException:
                    pass
            if self._spinner_task is not None and not self._spinner_task.done():
                self._spinner_task.cancel()
                # Await so the spinner's finally block runs (invalidate())
                # and asyncio doesn't emit "Task was destroyed" on loop
                # close. CancelledError on a cancelled task is expected.
                try:
                    await self._spinner_task
                except (asyncio.CancelledError, BaseException):
                    pass
            self._consumer_task = None
            self._spinner_task = None
            self._block_queue = None
            self._loop = None

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
                continue

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
        """True while the dispatcher is inside ``agent.handle()``.

        Tracked by the ``_in_respond`` flag rather than the user_messages
        queue's waiter count: the agent can ``await self.user_messages.get()``
        mid-turn (clarification flow), and during that wait the queue has
        a waiter — but the agent is genuinely thinking, not idle. The
        flag captures the dispatcher → handle() boundary directly.
        """
        if self._agent_task is None or self._agent_task.done():
            return False
        return self._in_respond

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
