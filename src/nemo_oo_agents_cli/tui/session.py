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
import concurrent.futures
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nemo_oo_agents import Agent

    from .commands import CommandRegistry
    from .config import Config
    from .frontend import Frontend
    from .session_manager import SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_model_name(full_name: str) -> str:
    """Convert a full model registry key to a short display name.

    Examples:
        "aws/anthropic/bedrock-claude-sonnet-4-5-v1" → "sonnet-4-5"
        "openai/gpt-4o" → "gpt-4o"
    """
    part = full_name.split("/")[-1]
    part = part.replace("bedrock-claude-", "").replace("bedrock-", "").replace("claude-", "")
    part = re.sub(r"-v\d+$", "", part)
    return part


def _build_prompt(config: "Config") -> str:
    """Build the dynamic input prompt."""
    return "❯ "


def _retrieve_future_exception(f: concurrent.futures.Future[None]) -> None:
    """Done-callback: retrieve the exception to silence warnings."""
    if f.cancelled():
        return
    try:
        f.exception()
    except RuntimeError:
        pass


async def _handle_python_shell(agent: "Agent", frontend: "Frontend") -> None:
    """Drop into an embedded IPython shell with the agent's full exec namespace."""
    import inspect
    import sys
    import typing as _typing

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
        from agentdoc.visibility import filter_module_globals

        agent_module = inspect.getmodule(type(agent))
        if agent_module is not None:
            ns.update(filter_module_globals(agent_module))
    except Exception:
        pass

    # Framework builtins (mirrors ActorRuntime.execute_code)
    try:
        from agentdoc import doc

        ns["doc"] = doc
        ns["help"] = doc
    except Exception:
        pass
    try:
        from agentdoc import pprint

        ns["pprint"] = pprint
    except Exception:
        pass

    loop = asyncio.get_running_loop()

    # Helper: run coroutines or thread-bound sync calls on the event loop thread.
    # IPython runs in an executor thread, so direct access to thread-bound objects
    # (SQLite, etc.) fails.  This dispatches everything to the event loop thread.
    def run(obj):
        """Execute on the event loop thread.

        Coroutine:  run(agent.respond("hi"))
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
        '  \x1b[2mExamples: run(agent.respond("hi"))  |  run(lambda: agent.events.get("356"))\x1b[0m\n'
        "  \x1b[2mCtrl+D to return to TUI.\x1b[0m\n"
    )

    def _embed() -> None:
        IPython.embed(
            user_ns=ns,
            banner1=banner,
            banner2="",
            exit_msg="\x1b[2mReturning to TUI...\x1b[0m\n",
            colors="neutral",
        )

    await loop.run_in_executor(None, _embed)


async def _handle_bash_shell(frontend: "Frontend") -> None:
    """Drop into an interactive bash shell, then return to the TUI."""
    import subprocess
    import sys

    from .output import TextOutput

    if not sys.stdin.isatty():
        await frontend.render(TextOutput("!bash requires an interactive terminal.", "warning"))
        return

    await frontend.stop_thinking()
    await frontend.render(TextOutput("Entering bash. Type 'exit' to return.", "info"))

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: subprocess.run(["/bin/bash"]))

    await frontend.render(TextOutput("Returned to TUI.", "info"))


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """Frontend-agnostic REPL loop.

    Owns all behavior: agent event subscription, streaming state, show_python
    decisions.  The frontend is a pure rendering surface.

    Args:
        frontend: Any object implementing the ``Frontend`` protocol.
        agent: An NeMo OO Agents agent with a ``respond(message)`` async method.
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

        # Streaming state (moved from _AgentStreamMixin)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_code: dict[str, str] = {}  # tool_call_id → code
        self._background_tasks: set[asyncio.Task] = set()  # fire-and-forget tasks

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
        """

        from .output import TextOutput
        from .tui_application import TUIApplication

        self._loop = asyncio.get_running_loop()

        async def _on_command(text: str) -> None:
            # Echo the submitted command to scrollback so exit commands
            # (which erase the live input region via erase_when_done)
            # leave a visible record of what the user ran.
            from rich.text import Text as _RT

            emit_text(_RT(f"❯ {text}", style="bold cyan"))

            # Errors bubble up to _drain_next's done-callback, which
            # surfaces them to scrollback and keeps draining.
            result = await self._handler.handle(text)
            if result.new_session_manager is not None:
                self._swap_session_manager(result.new_session_manager)
            if (
                result.compact_done
                and self._first_message
                and self._session_manager
                and not self._session_manager.user_named
            ):
                _t = asyncio.create_task(self._auto_name_session(self._first_message))
                self._background_tasks.add(_t)
                _t.add_done_callback(self._background_tasks.discard)
            if result.exit:
                app.exit()
                return
            if result.agent_message is not None:
                # Slash-command-generated agent turn — feed through the
                # same path as a typed message so the user bar, session
                # bookkeeping, and agent dispatch stay consistent.
                app.submit_message(result.agent_message)

        async def _on_bang(body: str) -> None:
            await self._handle_bang("!" + body)

        from .input_handler import SlashCommandCompleter
        from .theme import CATPPUCCIN_THEME, COLORS

        def _session_label() -> str:
            """Compose the label shown on the right of the rule just
            above the input: 'context ctx 20% · name [abc12345]'."""
            bits: list[str] = []
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

        app = TUIApplication(
            agent=self.agent,
            on_command=_on_command,
            on_bang=_on_bang,
            completer=SlashCommandCompleter(self.registry),
            session_label=_session_label,
        )

        # ── block rendering ──────────────────────────────────────────
        # emit_text(renderable) = render Rich object to ANSI, enqueue
        # it onto the app's single-consumer block queue. This is the
        # ONLY way content reaches the transcript in plan-c. Keeps
        # every producer on one serialised path — no races.
        import io as _io

        from rich.console import Console as _RichConsole

        _emit_console = _RichConsole(
            file=_io.StringIO(),
            force_terminal=True,
            color_system="256",
            width=120,
            theme=CATPPUCCIN_THEME,
        )

        def emit_text(renderable) -> None:
            """Render any Rich renderable / string to ANSI and enqueue.

            Width tracks the current terminal so background-bar blocks
            (e.g. the user-message panel) span the full row.
            """
            import shutil

            try:
                cols = max(shutil.get_terminal_size((120, 24)).columns, 40)
            except Exception:
                cols = 120
            _emit_console.width = cols
            buf = _io.StringIO()
            _emit_console.file = buf
            _emit_console.print(renderable)
            app.emit_block(buf.getvalue())

        # Route the frontend's own Rich console through emit_block too,
        # so slash-command outputs (rendered via frontend.render(...))
        # land in the block queue instead of the real stdout. This is
        # what makes /help, /model, /context etc. appear correctly in
        # the scrollback — without the redirect, their Rich writes
        # clobber prompt_toolkit's layout at the current cursor.
        #
        # Buffer writes until flush() so a single ``console.print(table)``
        # lands as ONE queue entry (one ``run_in_terminal`` hop) instead
        # of dozens of per-chunk enqueues. Rich flushes at the end of
        # every ``Console.print``.
        class _EmitStream:
            def __init__(self) -> None:
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
                    app.emit_block(chunk)

            def isatty(self) -> bool:
                return True

        # Swap the TUIConsole's Rich console for one that writes through
        # our block queue. Uses the public ``replace_console`` seam so we
        # don't reach into ``frontend._console.console`` directly.
        tui_console = getattr(self.frontend, "console", None)
        if tui_console is not None and hasattr(tui_console, "replace_console"):
            tui_console.replace_console(
                _RichConsole(
                    file=_EmitStream(),  # type: ignore[arg-type]
                    force_terminal=True,
                    color_system="256",
                    width=120,
                    theme=CATPPUCCIN_THEME,
                )
            )

        # Renderer subscribes Reasoning / ToolCallEvent / PythonOutput
        # and plugs itself into ``agent._render_message`` so ``self.message()``
        # output lands in submission order relative to code cells.
        from .agent_event_renderer import AgentEventRenderer

        renderer = AgentEventRenderer(
            agent=self.agent,
            emit_text=emit_text,
            show_python=lambda: self.show_python,
            pending_code=self._pending_code,
            colors=COLORS,
        )

        def _on_user_message(text: str) -> None:
            from rich.text import Text

            if self._session_manager is not None:
                self._session_manager.record_user(text)
            if self._first_message is None:
                self._first_message = text
                if (
                    self._session_manager is not None
                    and not self._session_manager.user_named
                    and not (self._session_manager.name or "").strip()
                ):
                    _t = asyncio.create_task(self._auto_name_session(text))
                    self._background_tasks.add(_t)
                    _t.add_done_callback(self._background_tasks.discard)

            # User-message bar: plain (non-bold) text on a grey background
            # that spans the full terminal width. Prompt glyph matches
            # the input line's BeforeInput marker. Each line pads to
            # ``cols`` individually so the background reaches the right
            # edge on every row (first line carries ``❯``; continuation
            # lines start flush-left).
            import shutil

            try:
                cols = max(shutil.get_terminal_size((120, 24)).columns, 40)
            except Exception:
                cols = 120
            lines_in = text.split("\n")
            padded: list[str] = []
            for i, line in enumerate(lines_in):
                shown = f" ❯ {line} " if i == 0 else f" {line} "
                padded.append(shown.ljust(cols))
            bar = Text("\n".join(padded), style=f"{COLORS['text']} on {COLORS['surface2']}")
            emit_text(bar)
            # New turn → reset the renderer's per-turn ``OO ─`` guard.
            renderer.reset_turn()

        # Assign the user-message callback after construction because
        # it closes over emit_text / renderer defined here.
        app.on_user_message = _on_user_message

        # Replace Session's logging-based loop exception handler with one
        # that surfaces every swallowed task exception into the scrollback.
        # Without this, any coroutine we schedule (spinner, _fire-driven
        # commands, background bookkeeping) that raises vanishes into
        # Python's logging module — users see 'nothing happened'.
        loop = asyncio.get_running_loop()

        def _loud_handler(_loop, context):
            import traceback as _tb

            msg = context.get("message", "")
            exc = context.get("exception")
            line = f"[asyncio] {msg}"
            if exc is not None:
                line += f" — {type(exc).__name__}: {exc}"
                if hasattr(exc, "__traceback__"):
                    line += "\n" + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
            app.emit_block(line + "\n")

        loop.set_exception_handler(_loud_handler)

        # Subscribe inside the try so any exception between attach and
        # app.run_async completion still fires renderer.detach in finally.
        try:
            renderer.attach()
            await app.run_async()
        except (KeyboardInterrupt, EOFError):
            await self.frontend.render(
                TextOutput("Interrupted by the user. Exiting TUI...", "warning")
            )
        finally:
            # Cancel any fire-and-forget session tasks (auto-naming,
            # post-compact naming) still running at shutdown so they
            # don't outlive the event loop with "Task was destroyed"
            # warnings.
            self._cancel_background_tasks()
            self._dump_exit_diagnostics()
            self.frontend.close()
            renderer.detach()
            if self._session_manager is not None:
                storage = getattr(self.agent, "_storage", None)
                if storage is not None and hasattr(storage, "save_snapshot"):
                    try:
                        storage.save_snapshot(self.agent)
                    except Exception:
                        pass
                self._session_manager.close()

    def _cancel_background_tasks(self) -> None:
        """Cancel pending fire-and-forget tasks tracked by this session.

        The set is populated by auto-naming and post-compact renaming;
        tasks that finish remove themselves via ``discard``. At shutdown
        anything still in the set is stale — cancel it so the loop can
        close cleanly.
        """
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        self._background_tasks.clear()

    # ------------------------------------------------------------------
    # Session manager swap (triggered by /session new)
    # ------------------------------------------------------------------

    def _swap_session_manager(self, new_sm: "SessionManager") -> None:
        """Close the current session and switch to *new_sm*."""
        if self._session_manager is not None:
            self._session_manager.close()
        self._session_manager = new_sm
        # Point the agent at the new storage so it doesn't write to the now-closed DB.
        if hasattr(self.agent, "_storage"):
            self.agent._storage = new_sm._storage
        # Propagate to registry and all command instances so /session export etc. use new ID.
        self.registry.session_manager = new_sm
        for cmd in self.registry.commands():
            cmd.session_manager = new_sm
        # Start a fresh trace for the new session so it gets its own .jsonl file.
        # Use the first 8 chars of the SQLite session UUID to correlate trace↔storage.
        try:
            from openinference_instrumentation_nemo_oo_agents import set_session

            from .session_manager import _make_trace_session_name

            set_session(_make_trace_session_name(new_sm.session_id or ""))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Session auto-naming
    # ------------------------------------------------------------------

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

        # !ipython → embedded IPython shell
        if cmd == "ipython":
            await _handle_python_shell(self.agent, self.frontend)
            return

        # !bash → interactive bash shell
        if cmd == "bash":
            await _handle_bash_shell(self.frontend)
            return

        # Other !commands → run through bash (not recorded as conversation turns)
        if not hasattr(self.agent, "bash"):
            await self.frontend.render(
                TextOutput(
                    "Direct bash commands (!) require an agent with bash support.",
                    "warning",
                )
            )
            return

        try:
            result = await self.agent.bash.run(cmd)  # type: ignore[union-attr]
            if result:
                await self.frontend.render(
                    BashOutput(
                        stdout=result.stdout or "",
                        stderr=result.stderr or "",
                        return_code=getattr(result, "return_code", 0),
                    )
                )
        except Exception as e:
            await self.frontend.render(TextOutput(f"Bash error: {e}", "error"))
