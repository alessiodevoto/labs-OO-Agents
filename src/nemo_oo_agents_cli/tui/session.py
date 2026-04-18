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


def _code_preview(code: str, max_cols: int = 100) -> str:
    """Return a compact preview of *code* for the activity line.

    Shape:
      * First line is comment (``#…``): keep the comment (white-styled
        downstream) AND the next non-blank code line (grey). Max 2.
      * First line is code: show just that line (grey). Max 1.

    Filters out lines matching ``return_result(...)`` — CodeAct
    boilerplate the user doesn't care about in a preview. Long lines
    truncate to ``max_cols`` with an ellipsis.
    """
    raw = [ln for ln in code.splitlines() if ln.strip()]
    # Drop the CodeAct-internal return_result(...) scaffolding.
    lines = [ln for ln in raw if not ln.lstrip().startswith("return_result(")]
    if not lines:
        return ""

    def _clip(ln: str) -> str:
        return ln if len(ln) <= max_cols else ln[: max_cols - 1] + "…"

    first = lines[0]
    if first.lstrip().startswith("#"):
        result = [_clip(first)]
        if len(lines) > 1:
            result.append(_clip(lines[1]))
        if len(lines) > 2:
            result[-1] += "…"
        return "\n".join(result)

    # No comment — one line only, suffix with … if there was more.
    clipped = _clip(first)
    if len(lines) > 1:
        clipped += "…"
    return clipped


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
        self._pending_input: str = ""  # pre-fill next prompt (from interrupted queue)
        self._pending_commands: list[str] = []  # slash commands queued during agent work

        # Streaming state (moved from _AgentStreamMixin)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe_fns: list = []
        self._pending_code: dict[str, str] = {}  # tool_call_id → code
        self._background_tasks: set[asyncio.Task] = set()  # fire-and-forget tasks
        self._agent_has_messaged: bool = False  # True after first message() in current turn

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

    # ------------------------------------------------------------------
    # Agent event subscription (moved from _AgentStreamMixin)
    # ------------------------------------------------------------------

    def _attach_agent(self, agent: "Agent") -> None:
        """Subscribe to agent events for real-time streaming display."""
        self._loop = asyncio.get_running_loop()
        self._install_exception_handler(self._loop)
        self._unsubscribe_fns.append(agent.event_manager.on("Reasoning", self._on_reasoning))
        self._unsubscribe_fns.append(agent.event_manager.on("ToolCallEvent", self._on_tool_call))
        self._unsubscribe_fns.append(agent.event_manager.on("PythonOutput", self._on_python_output))

    def _detach_agent(self) -> None:
        for fn in self._unsubscribe_fns:
            try:
                fn()
            except Exception:
                pass
        self._unsubscribe_fns.clear()
        self._restore_exception_handler()

    # ------------------------------------------------------------------
    # Event loop exception handler (replaces prompt_toolkit's broken one)
    # ------------------------------------------------------------------

    def _install_exception_handler(self, loop: asyncio.AbstractEventLoop) -> None:
        """Replace the event loop exception handler with one that shows useful info.

        prompt_toolkit's handler prints ``context.get("exception")`` but never
        prints ``context.get("message")``, so when asyncio fires an exception
        context without an ``exception`` key (e.g. "Task was destroyed but it
        is pending!") the user sees the useless ``Exception None``.
        """
        self._prev_exception_handler = loop.get_exception_handler()

        import logging
        import traceback as _tb

        _log = logging.getLogger("nemo_oo_agents.tui")

        def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            msg = context.get("message", "")
            exc = context.get("exception")
            # Log the full context for post-mortem debugging
            _log.warning("asyncio exception: %s (exception=%r)", msg, exc)
            if exc and hasattr(exc, "__traceback__"):
                _log.debug("".join(_tb.format_exception(type(exc), exc, exc.__traceback__)))

        loop.set_exception_handler(_handler)

    def _restore_exception_handler(self) -> None:
        prev = getattr(self, "_prev_exception_handler", None)
        if self._loop is not None:
            try:
                self._loop.set_exception_handler(prev)
            except Exception:
                pass

    def _clear_streaming_state(self) -> None:
        self._pending_code.clear()

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
        if not (max_context and max_event):
            return ""
        max_total = max_context + max_event
        if max_total <= 0:
            return ""
        pct = stats.total_tokens / max_total * 100
        return f"ctx {pct:.0f}%"

    def _print_input_rule(self) -> None:
        """Print a horizontal rule before the input prompt with session info right-aligned."""
        from .theme import COLORS

        console = getattr(getattr(self.frontend, "_console", None), "console", None)
        if console is None:
            return

        segments: list[str] = []
        usage = self._context_usage_label()
        if usage:
            segments.append(usage)

        if self._session_manager is not None:
            sm = self._session_manager
            short_id = (sm.session_id or "")[:8]
            name = sm.name
            if name and short_id:
                segments.append(f"{name} \\[{short_id}]")
            elif short_id:
                segments.append(f"\\[{short_id}]")

        label = " · ".join(segments)

        from rich.rule import Rule

        if label:
            console.print(
                Rule(
                    title=f"[{COLORS['overlay1']}]{label}[/]",
                    style=COLORS["surface1"],
                    align="right",
                )
            )
        else:
            console.print(Rule(style=COLORS["surface1"]))

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
    # Agent event callbacks (moved from _AgentStreamMixin)
    # ------------------------------------------------------------------

    def _on_reasoning(self, event) -> None:
        if self.show_python:
            return
        content = getattr(event, "content", "") or ""
        if not content.strip():
            return
        if self._loop is not None:
            from .output import ActivityLine

            fut = asyncio.run_coroutine_threadsafe(
                self.frontend.render(ActivityLine(content, kind="reasoning")),
                self._loop,
            )
            fut.add_done_callback(_retrieve_future_exception)

    def _on_tool_call(self, event) -> None:
        if getattr(event, "name", "") == "execute_python":
            tool_call_id = getattr(event, "tool_call_id", "")
            arguments = getattr(event, "arguments", {})
            if tool_call_id and isinstance(arguments, dict):
                code = arguments.get("code", "")
                if code:
                    self._pending_code[tool_call_id] = code
                    if not self.show_python:
                        from .output import ActivityLine

                        # Detect prefill executions and show a friendlier message
                        if tool_call_id.startswith("prefill_"):
                            preview = "Inspecting inputs..."
                        else:
                            preview = _code_preview(code)

                        if preview and self._loop is not None:
                            fut = asyncio.run_coroutine_threadsafe(
                                self.frontend.render(ActivityLine(preview, kind="code")),
                                self._loop,
                            )
                            fut.add_done_callback(_retrieve_future_exception)

    def _on_python_output(self, event) -> None:
        try:
            from context_blocks import ResultStatus

            is_complete = event.execution_status == ResultStatus.COMPLETE
        except ImportError:
            is_complete = True

        value = None
        if is_complete and getattr(event, "value", None) is not None:
            value = event.value

        from .output import CodeExecution

        execution = CodeExecution(
            tool_call_id=getattr(event, "tool_call_id", ""),
            code=self._pending_code.pop(getattr(event, "tool_call_id", ""), None),
            stdout=getattr(event, "stdout", None) or None,
            stderr=getattr(event, "stderr", None) or None,
            error=getattr(event, "error", None) or None,
            value=value,
        )

        if self.show_python and self._loop is not None:
            fut = asyncio.run_coroutine_threadsafe(self.frontend.render(execution), self._loop)
            fut.add_done_callback(_retrieve_future_exception)
            return

        # show_python=False (default): emit stdout/stderr of the cell as
        # indented ActivityLines so they appear just below the code
        # preview that fired a moment ago — ipython-notebook feel.
        if self._loop is None:
            return
        from .output import ActivityLine

        for stream_kind, payload in (("stdout", execution.stdout), ("stderr", execution.stderr)):
            if not payload:
                continue
            for line in str(payload).rstrip("\n").split("\n"):
                fut = asyncio.run_coroutine_threadsafe(
                    self.frontend.render(ActivityLine(line, kind=stream_kind)),  # type: ignore[arg-type]
                    self._loop,
                )
                fut.add_done_callback(_retrieve_future_exception)

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

        self._attach_agent(self.agent)

        async def _on_command(text: str) -> None:
            # Echo the submitted command to scrollback so exit commands
            # (which erase the live input region via erase_when_done)
            # leave a visible record of what the user ran. Styled bold
            # cyan to visually match the prompt glyph.
            from rich.text import Text as _RT

            emit_text(_RT(f"❯ {text}", style="bold cyan"))

            try:
                result = await self._handler.handle(text)
            except BaseException as exc:
                app.append_output(f"[command error] {type(exc).__name__}: {exc}\n")
                raise
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
                app._launch_agent(result.agent_message)

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
        from rich.markdown import Markdown

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
        class _EmitStream:
            def write(self, text: str) -> int:
                if text:
                    app.emit_block(text)
                return len(text)

            def flush(self) -> None:
                return None

            def isatty(self) -> bool:
                return True

        if hasattr(self.frontend, "_console"):
            self.frontend._console.console = _RichConsole(  # type: ignore[attr-defined]
                file=_EmitStream(),  # type: ignore[arg-type]
                force_terminal=True,
                color_system="256",
                width=120,
                theme=CATPPUCCIN_THEME,
            )

        # Track whether the agent has already emitted a markdown block
        # this turn — we only want the "OO ─" rule at the START of its
        # reply block, not before every message() call in a multi-msg
        # turn. Reset to False when the user submits a new message.
        agent_has_messaged = {"value": False}

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

            # The session rule lives as a live layout row right above
            # the input (see TUIApplication.session_rule window) — no
            # need to emit a separate transcript rule per turn.
            #
            # User-message bar: plain (non-bold) text on a grey
            # background bar that spans the full terminal width.
            # Prompt glyph matches the input line's BeforeInput marker.
            import shutil

            try:
                cols = max(shutil.get_terminal_size((120, 24)).columns, 40)
            except Exception:
                cols = 120
            # For multi-line messages we pad EACH line to ``cols``
            # individually so the grey background reaches the right
            # edge on every row. First line carries the ``❯`` prefix;
            # continuation lines start flush-left (no indent).
            lines_in = text.split("\n")
            padded: list[str] = []
            for i, line in enumerate(lines_in):
                shown = f" ❯ {line} " if i == 0 else f" {line} "
                padded.append(shown.ljust(cols))
            bar = Text("\n".join(padded), style=f"{COLORS['text']} on {COLORS['surface2']}")
            emit_text(bar)
            # New turn → reset the per-turn first-message guard.
            agent_has_messaged["value"] = False

        app._on_user_message = _on_user_message  # late-bound

        # Buffer messages emitted during a code cell — flushed after the
        # cell's PythonOutput event so message text always lands BELOW
        # the code/output block. In show_python mode this is essential
        # (otherwise markdown lands before the '─── oo python ───' rule);
        # in preview mode it also matches the expected reading order.
        pending_messages: list[str] = []

        from rich.rule import Rule as _Rule
        from rich.text import Text as _RT

        def _flush_messages() -> None:
            while pending_messages:
                text = pending_messages.pop(0)
                if not agent_has_messaged["value"]:
                    agent_has_messaged["value"] = True
                    emit_text(_Rule(_RT("OO ", style=COLORS["mauve"]), style="dim", align="left"))
                emit_text(Markdown(str(text)))

        if hasattr(self.agent, "_render_message"):

            def _render_msg(text: str) -> None:
                pending_messages.append(str(text))

            self.agent._render_message = _render_msg  # type: ignore[attr-defined]

        # ── replace the default event handlers with plan-c versions ──
        # Session._attach_agent's handlers schedule frontend.render via
        # run_coroutine_threadsafe, which means the activity line lands
        # in the queue LATER than a synchronously-emitted message block.
        # These sync handlers emit directly, so order matches event
        # firing order (ToolCall before self.message).
        self._detach_agent()
        self._loop = asyncio.get_running_loop()

        def _on_reasoning(event) -> None:
            from rich.text import Text

            content = getattr(event, "content", "") or ""
            if content.strip():
                emit_text(Text(content, style="dim italic"))

        def _on_tool_call(event) -> None:
            from rich.text import Text

            if getattr(event, "name", "") != "execute_python":
                return
            tool_call_id = getattr(event, "tool_call_id", "")
            arguments = getattr(event, "arguments", {})
            code = arguments.get("code", "") if isinstance(arguments, dict) else ""
            if not code:
                return
            self._pending_code[tool_call_id] = code
            # In show_python mode the full cell (oo python / code / oo
            # stdout / stdout) renders from _on_python_output — no
            # preview teaser needed.
            if self.show_python:
                return
            preview = (
                "Inspecting inputs..."
                if tool_call_id.startswith("prefill_")
                else _code_preview(code)
            )
            if not preview:
                return
            first_line = preview.split("\n", 1)[0]
            styled = Text(f"∴ {first_line}", style="dim")
            if first_line.lstrip().startswith("#"):
                styled.stylize("not dim", 0, len(first_line) + 2)
            if "\n" in preview:
                styled.append("\n  " + preview.split("\n", 1)[1], style="dim")
            emit_text(styled)

        def _on_python_output(event) -> None:
            from rich.rule import Rule
            from rich.syntax import Syntax
            from rich.text import Text as _RT

            tool_call_id = getattr(event, "tool_call_id", "")
            code = self._pending_code.pop(tool_call_id, None)
            if tool_call_id.startswith("prefill_"):
                # Still flush any pending messages so they don't land
                # below a LATER cell's output.
                _flush_messages()
                return

            stdout = str(getattr(event, "stdout", "") or "")
            stderr = str(getattr(event, "stderr", "") or "")

            # show_python=True: render a notebook-style cell — 'oo python'
            # rule, syntax-highlighted code, 'oo stdout' rule, stdout,
            # 'oo stderr' rule, stderr.
            if self.show_python and code:
                emit_text(Rule(_RT("oo python", style=COLORS["mauve"]), style="dim", align="left"))
                emit_text(
                    Syntax(code.strip(), "python", theme="monokai", background_color="default")
                )
                if stdout.strip():
                    emit_text(
                        Rule(_RT("oo stdout", style=COLORS["mauve"]), style="dim", align="left")
                    )
                    emit_text(_RT(stdout.rstrip("\n"), style=COLORS["text"]))
                if stderr.strip():
                    emit_text(
                        Rule(_RT("oo stderr", style=COLORS["red"]), style="dim", align="left")
                    )
                    emit_text(_RT(stderr.rstrip("\n"), style=COLORS["red"]))
                _flush_messages()
                return

            # Preview mode: stdout is for the agent; user sees results
            # via self.message(). Only show stderr so errors aren't
            # silent. Then flush any self.message() calls from this cell
            # so they appear BELOW the code preview.
            if stderr.strip():
                for line in stderr.rstrip("\n").split("\n"):
                    emit_text(_RT(f"  │ {line}", style="red"))
            _flush_messages()

        self._unsubscribe_fns.append(self.agent.event_manager.on("Reasoning", _on_reasoning))
        self._unsubscribe_fns.append(self.agent.event_manager.on("ToolCallEvent", _on_tool_call))
        self._unsubscribe_fns.append(self.agent.event_manager.on("PythonOutput", _on_python_output))

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
            app.append_output(line + "\n")

        loop.set_exception_handler(_loud_handler)

        try:
            await app.run_async()
        except (KeyboardInterrupt, EOFError):
            await self.frontend.render(
                TextOutput("Interrupted by the user. Exiting TUI...", "warning")
            )
        finally:
            self._dump_exit_diagnostics()
            self.frontend.close()
            self._detach_agent()
            if self._session_manager is not None:
                storage = getattr(self.agent, "_storage", None)
                if storage is not None and hasattr(storage, "save_snapshot"):
                    try:
                        storage.save_snapshot(self.agent)
                    except Exception:
                        pass
                self._session_manager.close()

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
        for cmd in self.registry._commands.values():
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
