# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Session — the REPL loop that glues a Frontend to an Agent.

``Session`` is frontend-agnostic: it reads input via ``frontend.get_input()``,
routes commands through ``CommandHandler``, and renders every output through
``frontend.render()``.  Both ``TerminalFrontend`` and ``WebFrontend`` are
drop-in replacements.

All *behavior* lives here — event subscription, streaming state, show_python
decisions.  Frontends are pure rendering.
"""

import asyncio
import io
import logging
import re
import shlex
import sys
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.console import Console as RichConsole
from rich.text import Text

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nemo_oo_agents import Agent
    from nemo_oo_agents.tools.shell_tools import ShellTools

    from .agent_event_renderer import AgentEventRenderer
    from .commands import CommandRegistry
    from .config import Config
    from .frontend import Frontend
    from .session_manager import SessionManager
    from .tui_application import TUIApplication


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_to_ansi256(hex_color: str) -> int:
    """Convert a ``#rrggbb`` hex string to the nearest xterm-256 index.

    Used when we render ANSI directly (e.g. the user-message bar) and
    can't rely on Rich's width/wrap logic to emit correctly-padded
    terminal output.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    # 6x6x6 color cube starting at index 16.
    def _q(v: int) -> int:
        # 0,95,135,175,215,255 — standard xterm cube steps.
        if v < 48:
            return 0
        if v < 115:
            return 1
        return (v - 35) // 40

    return 16 + 36 * _q(r) + 6 * _q(g) + _q(b)


def _build_user_bar(text: str, app: "TUIApplication", colors: dict) -> str:
    """Build a full-width highlighted user-message bar as raw ANSI.

    Bypasses Rich because reconciling Rich's wrap/crop/overflow logic
    with manual ``ljust`` padding is brittle across Rich versions and
    terminal emulators — direct CSI emission always renders the full
    width-spanning highlighted row the spec asks for.

    Each input line becomes one bar row:
      ``ESC[fg;bg m{prefix}{line}{padding}{ ESC[0m}\\n``
    where the first row carries the ``❯`` prompt glyph and
    continuation rows start flush-left.
    """
    # Prefer prompt_toolkit's live width — ``run_in_terminal`` will use
    # this number when writing above the prompt. Falls back to the
    # terminal_cols helper if the app output can't report.
    cols: int
    try:
        cols = app._app.output.get_size().columns  # type: ignore[attr-defined]
    except Exception:
        from .tui_application import terminal_cols

        cols = terminal_cols(minimum=40)
    cols = max(cols, 20)

    fg = _hex_to_ansi256(colors["text"])
    bg = _hex_to_ansi256(colors["surface2"])
    on = f"\x1b[38;5;{fg};48;5;{bg}m"
    off = "\x1b[0m"

    rows: list[str] = []
    for i, line in enumerate(text.split("\n")):
        shown = f" ❯ {line} " if i == 0 else f" {line} "
        # Clamp to cols so an overlong line becomes multiple bar rows
        # rather than wrapping chaotically at the terminal's edge.
        while len(shown) > cols:
            rows.append(f"{on}{shown[:cols]}{off}")
            shown = shown[cols:]
        rows.append(f"{on}{shown.ljust(cols)}{off}")
    return "\n".join(rows) + "\n"


class _EmitStream:
    """A ``Console.file`` target that batches writes into one ``emit_block``
    per ``flush()``.

    Rich's ``Console.print`` flushes at the end; without buffering each
    stylised chunk (many per print call) would enqueue a separate block
    and pay the ``run_in_terminal`` hop. Batching collapses them into
    one atomic scrollback block.
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._buf: list[str] = []

    def write(self, text: str) -> int:
        if text:
            self._buf.append(text)
        return len(text)

    def flush(self) -> None:
        if not self._buf:
            return
        chunk = "".join(self._buf)
        self._buf.clear()
        if chunk:
            self._emit(chunk)

    def isatty(self) -> bool:
        return True


def _short_model_name(full_name: str) -> str:
    """Convert a full model registry key to a short display name.

    Examples:
        "claude-sonnet-4-5" → "sonnet-4-5"
        "gpt-4o" → "gpt-4o"
    """
    part = full_name.split("/")[-1]
    part = part.replace("bedrock-claude-", "").replace("bedrock-", "").replace("claude-", "")
    part = re.sub(r"-v\d+$", "", part)
    return part


async def _handle_python_shell(agent: "Agent", frontend: "Frontend") -> None:
    """Drop into an embedded IPython shell with the agent's full exec namespace."""
    import inspect
    import sys
    import typing as _typing

    from prompt_toolkit.application import run_in_terminal

    from .output import TextOutput

    # Web frontends don't have a terminal — bail out early rather than hang
    if not sys.stdin.isatty():
        await frontend.render(
            TextOutput(
                "!python / !ipython requires an interactive terminal (not available in the web UI).",
                "warning",
            )
        )
        return

    try:
        import IPython  # type: ignore[import-not-found]
    except ImportError:
        await frontend.render(
            TextOutput(
                "IPython not installed. Run: uv add ipython  (or: pip install ipython)",
                "error",
            )
        )
        return

    # Build the same exec namespace the agent's CodeAct strategy uses:
    # module-level globals + self + framework builtins.
    ns: dict = {}
    try:
        from nemo_oo_agents.agentdoc.visibility import filter_module_globals

        agent_module = inspect.getmodule(type(agent))
        if agent_module is not None:
            ns.update(filter_module_globals(agent_module))
    except Exception:
        pass

    # Framework builtins (mirrors ActorRuntime.execute_code)
    try:
        from nemo_oo_agents.agentdoc import doc

        ns["doc"] = doc
        ns["help"] = doc
    except Exception:
        pass
    try:
        from nemo_oo_agents.agentdoc import pprint

        ns["pprint"] = pprint
    except Exception:
        pass

    loop = asyncio.get_running_loop()

    # Helper: run coroutines or thread-bound sync calls on the event loop thread.
    # IPython runs in an executor thread, so direct access to thread-bound objects
    # (SQLite, etc.) fails.  This dispatches everything to the event loop thread.
    def run(obj):
        """Execute on the event loop thread.

        Coroutine:  run(agent.handle("hi"))
        Callable:   run(lambda: agent.events.get("356"))
        """
        if asyncio.iscoroutine(obj):
            f = asyncio.run_coroutine_threadsafe(obj, loop)
        elif callable(obj):

            async def _w():
                result = obj()
                if asyncio.iscoroutine(result):
                    return await result
                return result

            f = asyncio.run_coroutine_threadsafe(_w(), loop)
        else:
            raise TypeError(f"Expected coroutine or callable, got {type(obj)}")
        return f.result(timeout=300)

    ns["self"] = agent
    ns["agent"] = agent  # convenience alias
    ns["loop"] = loop  # event loop for run_coroutine_threadsafe
    ns["run"] = run  # dispatch to event loop thread
    ns["asyncio"] = asyncio
    ns["typing"] = _typing
    ns["Annotated"] = _typing.Annotated
    ns["Any"] = _typing.Any
    ns["Literal"] = _typing.Literal
    ns["Optional"] = _typing.Optional
    ns["Union"] = _typing.Union

    # Stop any spinner before handing stdin to IPython.
    await frontend.stop_thinking()

    banner = (
        "\n\x1b[1;35m[NeMo OO Agents IPython]\x1b[0m\n"
        "  \x1b[2m'self' and 'agent' refer to the agent — same namespace as agent-generated code.\x1b[0m\n"
        "  \x1b[2mrun() dispatches to the event loop thread (needed for SQLite, async calls).\x1b[0m\n"
        '  \x1b[2mExamples: run(agent.handle("hi"))  |  run(lambda: agent.events.get("356"))\x1b[0m\n'
        "  \x1b[2mCtrl+D to return to TUI.\x1b[0m\n"
    )

    def _embed() -> None:
        # Restore real terminal streams so IPython's stdout isn't
        # captured by _StrayStreamForwarder (whose emit_block path
        # is blocked while run_in_terminal holds the lock).
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        try:
            IPython.embed(
                user_ns=ns,
                banner1=banner,
                banner2="",
                exit_msg="\x1b[2mReturning to TUI...\x1b[0m\n",
                colors="neutral",
            )
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err

    await run_in_terminal(_embed, in_executor=True)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """Frontend-agnostic REPL loop.

    Owns all behavior: agent event subscription, streaming state, show_python
    decisions.  The frontend is a pure rendering surface.

    Args:
        frontend: Any object implementing the ``Frontend`` protocol.
        agent: An NeMo OO Agents agent with a ``handle(notification)`` async method.
        config: Loaded ``Config`` (holds tui / agent sub-configs).
        registry: Pre-built ``CommandRegistry``.
        session_manager: Optional ``SessionManager`` for persisting turns.
    """

    def __init__(
        self,
        frontend: "Frontend",
        agent: "Agent",
        config: "Config",
        registry: "CommandRegistry",
        session_manager: "SessionManager | None" = None,
    ) -> None:
        from .commands import CommandHandler

        self.frontend = frontend
        self.agent = agent
        self.config = config
        self.registry = registry
        self._handler = CommandHandler(registry=registry, frontend=frontend)
        self._session_manager = session_manager
        self._first_message: str | None = None  # first user turn (for auto-naming)

        # Streaming state shared with the AgentEventRenderer: the
        # tool_call_id → code map that pairs a preview with its matching
        # ``PythonOutput`` event.
        self._pending_code: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task] = set()  # fire-and-forget tasks
        self._naming_futures: set = set()  # concurrent.futures.Future from agent-loop dispatch

        # Populated at the start of ``run()``; referenced by the handler
        # methods (``_on_command``, ``_on_user_message``, ``_loud_handler``,
        # etc.) so they can live as real methods instead of 240 lines of
        # nested closures inside ``run()``.
        self._app: TUIApplication | None = None
        self._renderer: AgentEventRenderer | None = None
        self._emit_console: RichConsole | None = None
        self._loud_handler_reentrant: bool = False
        # Own ShellTools for bang (!) commands — avoids cross-loop issues
        # when the agent's shell was created on a different event loop.
        self._bang_shell: ShellTools | None = None

    @property
    def show_python(self) -> bool:
        """Whether to display full Python code execution panels."""
        return self.config.tui.show_python

    @show_python.setter
    def show_python(self, value: bool) -> None:
        self.config.tui.show_python = value

    @property
    def session_id(self) -> str | None:
        return self._session_manager.session_id if self._session_manager else None

    def _context_usage_label(self) -> str:
        """Compact ``"ctx N%"`` label from the most recent ContextWindowStats.

        Returns an empty string if no generation has run yet, or if the
        agent/runtime can't supply a bounded context window (no max).
        """
        stats = getattr(self.agent, "context_stats", None)
        if stats is None:
            return ""
        max_context = stats.max_context_tokens
        max_event = stats.max_event_tokens
        if max_context and max_event:
            max_total = max_context + max_event
        elif stats.model_context_window:
            max_total = stats.model_context_window
        else:
            return ""
        if max_total <= 0:
            return ""
        pct = stats.total_tokens / max_total * 100
        return f"ctx {pct:.0f}%"

    # ------------------------------------------------------------------
    # Exit diagnostics
    # ------------------------------------------------------------------

    def _dump_exit_diagnostics(self) -> None:
        """Print pending tasks, threads, and subprocesses on exit for debugging hangs."""
        import sys
        import threading

        lines: list[str] = []

        # Pending asyncio tasks
        try:
            pending = [t for t in asyncio.all_tasks() if not t.done()]
            if pending:
                lines.append(f"Pending asyncio tasks ({len(pending)}):")
                for t in pending:
                    coro = t.get_coro()
                    name = getattr(coro, "__qualname__", str(coro))
                    lines.append(f"  - {t.get_name()}: {name}")
        except RuntimeError:
            pass

        # Non-daemon threads still alive
        alive = [
            t
            for t in threading.enumerate()
            if t.is_alive() and not t.daemon and t != threading.main_thread()
        ]
        if alive:
            lines.append(f"Live non-daemon threads ({len(alive)}):")
            for t in alive:
                lines.append(f"  - {t.name} (ident={t.ident})")

        # Background tasks tracked by this session
        bg = [t for t in self._background_tasks if not t.done()]
        if bg:
            lines.append(f"Background session tasks ({len(bg)}):")
            for t in bg:
                lines.append(f"  - {t.get_name()}")

        if lines:
            sys.stderr.write("\n\033[2m[exit diagnostics]\n")
            for line in lines:
                sys.stderr.write(f"  {line}\n")
            sys.stderr.write("\033[0m\n")
            sys.stderr.flush()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Drive the single long-lived ``TUIApplication`` REPL.

        One Application owns the whole terminal: output scrolls above
        the live prompt region via a single-consumer block queue, so
        producers (command dispatch, agent events, messages) render in
        strict submission order and there's no handoff race that drops
        the first keystroke between turns.

        The handler logic lives in instance methods (``_on_command``,
        ``_on_user_message``, ``_emit_text``, ``_loud_handler``, …);
        ``run`` is just the wiring.
        """
        from .agent_event_renderer import AgentEventRenderer
        from .input_handler import SlashCommandCompleter
        from .output import TextOutput
        from .theme import CATPPUCCIN_THEME
        from .tui_application import TUIApplication

        # Save terminal attributes so we can restore them on exit, even
        # if prompt_toolkit crashes without resetting the terminal.
        self._saved_termios = None
        try:
            import termios

            if sys.stdin.isatty():
                self._saved_termios = termios.tcgetattr(sys.stdin.fileno())
        except (ImportError, OSError):
            pass

        # The block-rendering Rich Console: re-used across ``_emit_text``
        # calls with width reset per-call to track live terminal width.
        self._emit_console = RichConsole(
            file=io.StringIO(),
            force_terminal=True,
            color_system="256",
            width=120,
            theme=CATPPUCCIN_THEME,
        )

        self._app = TUIApplication(
            agent=self.agent,
            on_command=self._on_command,
            on_bang=self._on_bang,
            completer=SlashCommandCompleter(self.registry),
            session_label=self._session_label,
            config=self.config,
        )
        # Wire agent_run into all commands so they can dispatch mutations
        # to the agent thread via self.agent_run(fn).
        for cmd in self.registry.commands():
            cmd._agent_run = self._app.agent_run
        # Wire the user-bar render + TUIUserInput log on the channel's
        # on_get hook so the echo fires when the dispatcher (or agent
        # code mid-turn) actually dequeues the message — symmetric across
        # both consumer paths, which is why the hook lives on the queue
        # and not on the dispatcher loop. self.agent is typed as Agent;
        # the queue is on BaseTUIAgent. getattr matches the existing
        # convention in tui_application.py for the same lookup.
        queue = getattr(self.agent, "_user_messages_in", None)
        if queue is not None:

            def _on_user_message_ui_thread(text: str) -> None:
                app = self._app
                loop = getattr(app, "_loop", None) if app is not None else None
                if loop is None:
                    self._on_user_message(text)
                    return
                try:
                    on_ui_loop = asyncio.get_running_loop() is loop
                except RuntimeError:
                    on_ui_loop = False
                if on_ui_loop:
                    self._on_user_message(text)
                else:
                    loop.call_soon_threadsafe(self._on_user_message, text)

            queue.set_on_get(_on_user_message_ui_thread)

        # Swap the frontend's Rich Console for one that writes through
        # our block queue, so slash-command output (e.g. /help tables)
        # lands in scrollback instead of clobbering the live prompt.
        tui_console = getattr(self.frontend, "console", None)
        if tui_console is not None and hasattr(tui_console, "replace_console"):
            tui_console.replace_console(
                RichConsole(
                    file=_EmitStream(self._app.emit_block),  # type: ignore[arg-type]
                    force_terminal=True,
                    color_system="256",
                    width=120,
                    theme=CATPPUCCIN_THEME,
                )
            )

        self._renderer = AgentEventRenderer(
            agent=self.agent,
            emit_text=self._emit_text,
            show_python=lambda: self.show_python,
            pending_code=self._pending_code,
            colors=self._colors,
        )

        # Replace Python's default asyncio exception handler with one
        # that surfaces every swallowed task exception into the TUI.
        # Without this, any coroutine we schedule (spinner, commands,
        # background bookkeeping) that raises vanishes into logging and
        # the user sees "nothing happened".
        self._startup_loop = asyncio.get_running_loop()
        self._prev_exception_handler = self._startup_loop.get_exception_handler()
        self._startup_loop.set_exception_handler(self._loud_handler)

        # Subscribe inside the try so any exception between attach and
        # ``app.run_async`` completion still fires ``renderer.detach``
        # in the finally.
        try:
            self._renderer.attach()
            await self._app.run_async()
        except (KeyboardInterrupt, EOFError):
            await self.frontend.render(
                TextOutput("Interrupted by the user. Exiting TUI...", "warning")
            )
        finally:
            # Order matters:
            # 1. Detach the renderer FIRST. Clears agent._render_message
            #    so any post-shutdown self.message() call (e.g. from
            #    save_snapshot) doesn't write through a dead emit_text.
            # 2. Cancel AND AWAIT fire-and-forget tasks so they don't
            #    trip "Task was destroyed but it is pending" warnings
            #    when the loop closes.
            # 3. Diagnostics, frontend close, snapshot, session close.
            self._renderer.detach()
            # Shut down spawned jobs before cancelling background tasks
            # so generator finally blocks run cleanly.
            qm = getattr(self.agent, "queue_manager", None)
            if qm is not None:
                await qm.shutdown()
            await self._cancel_background_tasks()
            if self._bang_shell is not None:
                await self._bang_shell.close()
                self._bang_shell = None
            self._dump_exit_diagnostics()
            self.frontend.close()
            if self._session_manager is not None:
                storage = getattr(self.agent, "_storage", None)
                if storage is not None and hasattr(storage, "save_snapshot"):
                    try:
                        app = getattr(self, "_app", None)
                        if app is not None:
                            app.agent_run(lambda: storage.save_snapshot(self.agent))
                        else:
                            # No agent loop — safe to call inline (single-threaded).
                            storage.save_snapshot(self.agent)
                    except Exception:
                        logger.debug("save_snapshot on shutdown failed", exc_info=True)
                self._session_manager.close()
            # Restore the previous exception handler so we don't pin
            # this Session or route unrelated failures through a torn-down TUI.
            if self._startup_loop is not None:
                self._startup_loop.set_exception_handler(self._prev_exception_handler)
            self._restore_terminal()
            self._print_exit_message()

    def _restore_terminal(self) -> None:
        """Best-effort restoration of terminal state on exit.

        prompt_toolkit normally restores the terminal, but on crashes,
        signals, or unhandled exceptions the terminal can be left in raw
        mode with echo disabled. We saved termios attrs at startup and
        restore them here as a safety net.
        """
        try:
            import termios

            if self._saved_termios is not None and sys.stdin.isatty():
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, self._saved_termios)
                return
        except (ImportError, OSError):
            pass
        # Fallback: if termios isn't available or failed, shell out to stty
        try:
            import subprocess

            if sys.stdin.isatty():
                subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False)
        except Exception:
            pass

    def _print_exit_message(self) -> None:
        """Print the parting line to stderr with session id + name.

        Runs after session_manager.close() so the persisted session is
        what the user sees referenced. Goes through ``sys.stderr``
        (not the frontend) because the prompt_toolkit Application has
        already cleaned up its terminal state and the frontend is
        closed.
        """
        sm = self._session_manager
        if sm is not None:
            short = (sm.session_id or "")[:8]
            name = sm.name
            if name and short:
                tag = f"{name} [{short}]"
            elif short:
                tag = f"[{short}]"
            else:
                tag = ""
        else:
            tag = ""
        suffix = f" — {tag}" if tag else ""
        sys.stderr.write(f"\n\x1b[2mGoodbye! Stay vibing.{suffix}\x1b[0m\n")
        if sm is not None and short:
            sys.stderr.write(
                f"\x1b[2mResume this session with \x1b[0m\x1b[1mnemo oo tui -c {short}\x1b[0m\n"
            )

    # ------------------------------------------------------------------
    # Handlers — driven by TUIApplication, called in run()'s event loop.
    # Each closes over ``self._app`` / ``self._renderer`` / ``self._emit_console``
    # set up in ``run()``. Don't call any of these before ``run()`` has
    # started; calling an assertion-gated attribute access ("``self._app``
    # is None") raises a descriptive error.
    # ------------------------------------------------------------------

    @property
    def _colors(self) -> dict[str, str]:
        """Theme colour table — reads the current theme so ``/theme`` has
        effect without restarting the app."""
        from .theme import COLORS

        return COLORS

    def _emit_text(self, renderable: Any) -> None:
        """Render a Rich renderable → ANSI → enqueue to the block queue.

        This is the single path every producer takes: user-message bars,
        code previews, agent markdown, slash-command echoes. Width tracks
        the live terminal so full-width blocks span every column.
        """
        assert self._emit_console is not None and self._app is not None
        from .tui_application import terminal_cols

        self._emit_console.width = terminal_cols(minimum=40)
        buf = io.StringIO()
        self._emit_console.file = buf
        self._emit_console.print(renderable)
        self._app.emit_block(buf.getvalue())

    async def _on_command(self, text: str) -> None:
        """Handle one slash command submitted via the input or the queue.

        Echoes ``❯ /cmd`` to scrollback so exit commands leave a record
        (the live input region is erased on shutdown), then dispatches
        via ``CommandHandler``. Result flags drive session-manager swap,
        auto-naming, exit, and slash-generated agent turns.
        """
        assert self._app is not None

        self._emit_text(Text(f"❯ {text}", style="bold cyan"))
        result = await self._handler.handle(text)
        if result.new_session_manager is not None:
            # Cancel the running agent turn so it doesn't keep working
            # in the stale session after /clear or /session new.
            if self._app._agent_task is not None and not self._app._agent_task.done():
                self._app._agent_task.cancel()
                try:
                    await self._app._agent_task
                except asyncio.CancelledError:
                    pass
            await self._swap_session_manager(result.new_session_manager)
            self._first_message = None
        if (
            result.compact_done
            and self._first_message
            and self._session_manager
            and not self._session_manager.user_named
        ):
            self._name_session_on_agent_loop(self._first_message)
        if result.exit:
            self._app.exit()
            return
        if result.slash_result is not None:
            # Post the full SlashCommandResult to the slash_commands queue.
            # The agent receives it in notification["slash_commands"] with
            # the actual Python value preserved (not stringified).
            slash_ch = getattr(self.agent, "_slash_commands_in", None)
            if slash_ch is not None:
                slash_ch.put(result.slash_result)
                # Ensure the dispatcher wakes up to process the queued result
                self._app._ensure_dispatcher_task()
            else:
                # Fallback: submit as text if queue not available
                self._app.submit_message(str(result.slash_result))
        elif result.agent_message is not None:
            # Slash-command-generated agent turn — feed through the same
            # path as a typed message so the user bar, session bookkeeping,
            # and agent dispatch stay consistent.
            self._app.submit_message(result.agent_message)

    async def _on_bang(self, body: str) -> None:
        """Dispatch a ``!shell-command`` body (leading ``!`` already stripped)."""
        await self._handle_bang("!" + body)

    def _session_label(self) -> str:
        """Right-aligned session label on the rule above the input:
        ``14:32 · sonnet-4-5 · ctx 20% · my-session [abc12345]``."""
        import datetime as _dt

        bits: list[str] = []
        # Custom toolbar snippet (set via /toolbar command)
        custom = self._eval_toolbar_snippet()
        if custom:
            bits.append(custom)
        else:
            # Default: show current time + model
            bits.append(_dt.datetime.now().strftime("%H:%M"))
            bits.append(_short_model_name(self.config.tui.default_model))
        usage = self._context_usage_label()
        if usage:
            bits.append(usage)
        if self._session_manager is not None:
            sm = self._session_manager
            short = (sm.session_id or "")[:8]
            name = sm.name
            if name and short:
                bits.append(f"{name} [{short}]")
            elif short:
                bits.append(f"[{short}]")
        return " · ".join(bits)

    def _eval_toolbar_snippet(self) -> str:
        """Evaluate the custom toolbar snippet, returning its result or empty string.

        Snippets run with full Python access — this is a local CLI
        where the user already has arbitrary code execution via the
        agent REPL, so no sandboxing is attempted.
        """
        snippet = self.config.tui.toolbar_snippet
        if not snippet:
            return ""
        try:
            import datetime as _dt

            result = eval(
                snippet,
                {},
                {
                    "datetime": _dt,
                    "config": self.config,
                    "model": self.config.tui.default_model,
                    "short_model": _short_model_name(self.config.tui.default_model),
                    "time": _dt.datetime.now().strftime("%H:%M"),
                    "agent": self.agent,
                },
            )
            return str(result) if result is not None else ""
        except Exception:
            logger.debug("toolbar snippet failed: %s", snippet, exc_info=True)
            return ""

    def _on_user_message(self, text: str) -> None:
        """Render the user's submitted text as a full-width grey bar and
        reset per-turn renderer state.

        Also triggers first-message auto-naming and the session
        manager's user-record bookkeeping.
        """
        assert self._renderer is not None and self._app is not None

        if self._session_manager is not None:
            self._session_manager.record_user(text)
        if self._first_message is None:
            self._first_message = text
            if (
                self._session_manager is not None
                and not self._session_manager.user_named
                and not (self._session_manager.name or "").strip()
            ):
                self._name_session_on_agent_loop(text)

        self._app.emit_block(_build_user_bar(text, self._app, self._colors))
        self._renderer.reset_turn()

    def _loud_handler(self, _loop: asyncio.AbstractEventLoop, context: dict) -> None:
        """asyncio exception handler that surfaces every swallowed task
        exception into the scrollback instead of Python's logging.

        Guards against re-entry: if ``emit_block`` itself raises, the
        asyncio loop would call us back with the new exception, yielding
        unbounded recursion. On re-entry we fall back to a bare stderr
        write.
        """
        assert self._app is not None

        msg = context.get("message", "")
        exc = context.get("exception")
        # --- gl-212 diagnostics: enrich "bound to a different event loop" ---
        if exc is not None and "bound to a different event loop" in str(exc):
            startup_loop = getattr(self, "_startup_loop", None)
            diag_lines = [
                "[gl-212] asyncio.Lock bound to a different event loop — diagnostic dump:",
                f"  handler loop: id={id(_loop):#x}",
                f"  startup loop: id={id(startup_loop):#x} (same={startup_loop is _loop})",
                f"  exception: {exc!r}",
            ]
            # Inspect known Lock instances on the agent
            try:
                shell = getattr(self.agent, "shell", None)
                if shell is not None:
                    bash = getattr(shell, "_session", None)
                    if bash is not None:
                        lock = getattr(bash, "_lock", None)
                        diag_lines.append(f"  BashSession._lock: {lock!r}")
                        if lock is not None and hasattr(lock, "_loop"):
                            diag_lines.append(
                                f"    lock._loop: id={id(lock._loop):#x} (same={lock._loop is _loop})"
                            )
            except Exception as e:
                diag_lines.append(f"  [BashSession inspection failed: {e}]")
            try:
                actor = getattr(self.agent, "_actor", None)
                if actor is not None:
                    gen_lock = getattr(actor, "_generation_lock", None)
                    diag_lines.append(f"  Actor._generation_lock: {gen_lock!r}")
                    if gen_lock is not None and hasattr(gen_lock, "_loop"):
                        diag_lines.append(
                            f"    lock._loop: id={id(gen_lock._loop):#x} (same={gen_lock._loop is _loop})"
                        )
            except Exception as e:
                diag_lines.append(f"  [Actor inspection failed: {e}]")
            diag_lines.append("  Suggestion: restart the TUI. File details on gl#212.")
            diag_lines.append("")
            diag_msg = "\n".join(diag_lines)
            if self._loud_handler_reentrant:
                err = sys.__stderr__
                if err is not None:
                    err.write(diag_msg)
                    err.flush()
            else:
                self._loud_handler_reentrant = True
                try:
                    self._app.emit_block(diag_msg)
                except Exception:
                    # emit_block failed (plausible during degraded loop state) —
                    # fall back to stderr so the diagnostic isn't lost.
                    err = sys.__stderr__
                    if err is not None:
                        err.write(diag_msg)
                        err.flush()
                finally:
                    self._loud_handler_reentrant = False
            # Fall through to normal handler for the full traceback
        # litellm's LiteLLMAiohttpTransport recreates its cached aiohttp
        # ClientSession on error-recovery / loop-mismatch / session-closed
        # paths without awaiting close() on the old one. When GC reaps the
        # orphan, aiohttp's finalizer fires this warning via
        # call_exception_handler. It's upstream noise, not our bug, and
        # drowns real diagnostics — drop it before formatting.
        if msg == "Unclosed client session" or msg == "Unclosed connector":
            return
        task = context.get("task")
        # litellm's LoggingWorker._worker_loop tasks get orphaned when the
        # global singleton detects an event loop change and drops the old
        # _worker_task reference without cancelling it. Harmless upstream noise.
        if "Task was destroyed" in msg and task is not None and "LoggingWorker" in repr(task):
            return
        future = context.get("future")
        source_tb = context.get("source_traceback")
        line = f"[asyncio] {msg}"
        if exc is not None:
            line += f" — {type(exc).__name__}: {exc}"
            if hasattr(exc, "__traceback__"):
                line += "\n" + "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
        # Non-exception contexts (e.g. "Task was destroyed but it is pending!")
        # carry the offending task/future and — when PYTHONASYNCIODEBUG=1 — a
        # source_traceback pointing at where it was created.
        if task is not None:
            line += f"\n  task={task!r}"
        if future is not None and future is not task:
            line += f"\n  future={future!r}"
        if source_tb:
            line += "\n  source_traceback (where task was created, enable PYTHONASYNCIODEBUG=1):\n"
            line += "".join(traceback.format_list(source_tb))
        # Other keys asyncio sometimes supplies (handle, protocol, transport,
        # socket, peername, client_session, ...). 2000 chars per field covers
        # aiohttp ``ClientSession`` reprs (~1.1KB — connector/base_url/auth
        # all land in the middle) without letting a pathologically huge
        # transport (SSL state, large buffers) swamp the scrollback.
        from nemo_oo_agents.agentdoc import truncating_pformat

        _known = ("message", "exception", "task", "future", "source_traceback")
        for k, v in context.items():
            if k in _known:
                continue
            line += f"\n  {k}={truncating_pformat(v, max_chars=2000)}"
        line += "\n"

        if self._loud_handler_reentrant:
            err = sys.__stderr__
            if err is not None:
                try:
                    err.write(line)
                    err.flush()
                except Exception:
                    pass
            return
        self._loud_handler_reentrant = True
        try:
            self._app.emit_block(line)
        except Exception as inner:
            err = sys.__stderr__
            if err is not None:
                try:
                    err.write(f"[loud_handler fallback] {inner}\n{line}")
                    err.flush()
                except Exception:
                    pass
        finally:
            self._loud_handler_reentrant = False

    def _fire_and_forget(self, coro) -> asyncio.Task:
        """Schedule a coroutine as a tracked background task.

        Tracked means: cancelled at shutdown by ``_cancel_background_tasks``,
        and self-removing from the set when it finishes so the set
        doesn't leak references to completed tasks.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _cancel_background_tasks(self) -> None:
        """Cancel and await pending fire-and-forget tasks.

        The set is populated by auto-naming and post-compact renaming;
        tasks that finish remove themselves via ``discard``. At shutdown
        anything still in the set is stale. We cancel then ``gather``
        so the cancellation actually propagates before the loop closes —
        without the await asyncio emits "Task was destroyed but it is
        pending" on some orderings.
        """
        pending = [t for t in self._background_tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()
        # Cancel any naming futures dispatched to the agent loop.
        naming = getattr(self, "_naming_futures", None)
        if naming:
            for f in list(naming):
                f.cancel()
            naming.clear()

    # ------------------------------------------------------------------
    # Session manager swap (triggered by /session new)
    # ------------------------------------------------------------------

    async def _swap_session_manager(self, new_sm: "SessionManager") -> None:
        """Close the current session and switch to *new_sm*."""
        # Shut down spawned jobs and flush all queue channels so stale
        # items from the old session don't leak into the new one.
        qm = getattr(self.agent, "queue_manager", None)
        if qm is not None:
            await qm.shutdown()
            for name in qm.names():
                ch = qm.get_channel(name)
                if ch.mode == "queue":
                    ch.flush()
        if self._session_manager is not None:
            # Save snapshot before closing so /clear, /session new, and
            # /session resume don't lose the current session's self.v/todo.
            storage = getattr(self.agent, "_storage", None)
            if storage is not None and hasattr(storage, "save_snapshot"):
                try:
                    app = getattr(self, "_app", None)
                    if app is not None:
                        app.agent_run(lambda: storage.save_snapshot(self.agent))
                    else:
                        storage.save_snapshot(self.agent)
                except Exception:
                    logger.debug("save_snapshot on session swap failed", exc_info=True)
            self._session_manager.close()
        self._session_manager = new_sm
        # Point the agent at the new storage AND repoint the agent's
        # stable EventManager at the new backend. The set_backend call
        # is what keeps subscribers (e.g. AgentEventRenderer) alive
        # across the swap.
        if hasattr(self.agent, "_storage"):

            def _do_swap():
                self.agent._storage = new_sm._storage
                self.agent.event_manager.set_backend(new_sm._storage.event_backend)

            app = getattr(self, "_app", None)
            if app is not None:
                app.agent_run(_do_swap)
            else:
                _do_swap()
        # Propagate to registry and all command instances so /session export etc. use new ID.
        self.registry.session_manager = new_sm
        for cmd in self.registry.commands():
            cmd.session_manager = new_sm
        # Start a fresh trace for the new session so it gets its own .jsonl file.
        # Use the first 8 chars of the SQLite session UUID to correlate trace↔storage.
        try:
            from nemo_oo_agents.tracing import set_session

            from .session_manager import _make_trace_session_name

            set_session(_make_trace_session_name(new_sm.session_id or ""))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Session auto-naming
    # ------------------------------------------------------------------

    def _name_session_on_agent_loop(self, first_message: str) -> None:
        """Dispatch auto-naming to the agent loop to avoid cross-thread sqlite access."""
        agent_loop = getattr(self._app, "_agent_loop", None)
        if isinstance(agent_loop, asyncio.AbstractEventLoop) and agent_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._auto_name_session(first_message), agent_loop
            )
            # Track for cancellation on shutdown. concurrent.futures.Future isn't
            # awaitable so we keep a separate set from _background_tasks.
            self._naming_futures.add(future)
            future.add_done_callback(self._naming_futures.discard)
        else:
            # No agent loop available — fall back to local scheduling.
            self._fire_and_forget(self._auto_name_session(first_message))

    async def _auto_name_session(self, first_message: str) -> None:
        """Generate and persist a session name from the first user message."""
        if self._session_manager is None or self._session_manager.user_named:
            return
        try:
            name = await self.agent.name_session(first_message[:400])  # type: ignore[union-attr]
            name = str(name).strip().strip('"').strip("'")[:60]
            if name and not self._session_manager.user_named:
                self._session_manager.rename(name, user_named=False)
        except Exception:
            # Fallback: first few words of the message
            words = first_message.split()
            fallback = " ".join(words[:5])[:60]
            if fallback:
                self._session_manager.rename(fallback, user_named=False)

    # ------------------------------------------------------------------
    # Bang (!) command routing
    # ------------------------------------------------------------------

    async def _handle_bang(self, user_input: str) -> None:
        from .output import BashOutput, TextOutput

        cmd = user_input[1:].strip()
        if not cmd:
            return

        # !commands → run through shell (not recorded as conversation turns)
        if not hasattr(self.agent, "shell"):
            await self.frontend.render(
                TextOutput(
                    "Direct bash commands (!) require an agent with shell support.",
                    "warning",
                )
            )
            return

        try:
            shell = await self._get_bang_shell()
            result = await shell.run(cmd)
            if result:
                await self.frontend.render(
                    BashOutput(
                        stdout=result.stdout or "",
                        stderr=result.stderr or "",
                        return_code=result.returncode,
                        command=cmd,
                    )
                )
        except Exception as e:
            await self.frontend.render(TextOutput(f"Bash error: {e}", "error"))

    async def _get_bang_shell(self) -> "ShellTools":
        """Return (lazily creating) a ShellTools owned by the TUI for bang commands.

        The agent's ShellTools may have its asyncio.Lock bound to a different
        event loop.  This dedicated instance is created on the TUI's loop,
        avoiding "attached to a different loop" errors.

        Syncs cwd from the agent's shell on each call so the two stay
        in step when the agent changes directory during a session.
        """
        from nemo_oo_agents.tools.shell_tools import ShellTools

        if self._bang_shell is None:
            cwd = self.agent.shell.cwd if hasattr(self.agent, "shell") else "."
            self._bang_shell = ShellTools(cwd=cwd)
        elif hasattr(self.agent, "shell"):
            agent_cwd = str(self.agent.shell.cwd)
            if str(self._bang_shell.cwd) != agent_cwd:
                await self._bang_shell.run(f"cd {shlex.quote(agent_cwd)}")
        return self._bang_shell
