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


def _code_preview(code: str, max_lines: int = 2) -> str:
    """Return the first meaningful non-blank lines of *code* for activity preview."""
    lines = [ln for ln in code.splitlines() if ln.strip()]
    preview = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        preview += "..."
    return preview


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

    ns["self"] = agent
    ns["agent"] = agent  # convenience alias
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
        '  \x1b[2mExample: await agent.respond("hello")  |  doc(self)  |  self.bash\x1b[0m\n'
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

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _embed)


async def _handle_bash_shell(frontend: "Frontend") -> None:
    """Drop into an interactive bash shell, then return to the TUI."""
    import subprocess
    import sys

    from .output import TextOutput

    if not sys.stdin.isatty():
        await frontend.render(
            TextOutput("!bash requires an interactive terminal.", "warning")
        )
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
        self._unsubscribe_fns: list = []
        self._pending_code: dict[str, str] = {}  # tool_call_id → code

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
        self._unsubscribe_fns.append(agent.event_manager.on("reasoning", self._on_reasoning))
        self._unsubscribe_fns.append(agent.event_manager.on("tool_call", self._on_tool_call))
        self._unsubscribe_fns.append(
            agent.event_manager.on("python_output", self._on_python_output)
        )

    def _detach_agent(self) -> None:
        for fn in self._unsubscribe_fns:
            try:
                fn()
            except Exception:
                pass
        self._unsubscribe_fns.clear()

    def _clear_streaming_state(self) -> None:
        self._pending_code.clear()

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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the REPL until the user exits or EOF."""
        from .output import TextOutput

        self._attach_agent(self.agent)

        # Wire agent.message() → frontend.render() directly (no event callback)
        if hasattr(self.agent, "_render_message"):
            from .output import AgentMessage

            loop = asyncio.get_running_loop()
            frontend = self.frontend

            def _render_msg(text: str) -> None:
                asyncio.run_coroutine_threadsafe(frontend.render(AgentMessage(content=text)), loop)

            self.agent._render_message = _render_msg  # type: ignore[attr-defined]

        try:
            while True:
                prompt = _build_prompt(self.config)
                try:
                    user_input = await self.frontend.get_input(prompt)
                except EOFError:
                    await self.frontend.render(
                        TextOutput("Interrupted by the user. Exiting TUI...", "warning")
                    )
                    break
                except KeyboardInterrupt:
                    await self.frontend.render(
                        TextOutput("Interrupted by the user. Exiting TUI...", "warning")
                    )
                    break

                if not user_input:
                    continue

                # Slash commands
                if user_input.startswith("/"):
                    result = await self._handler.handle(user_input)
                    if result.new_session_manager is not None:
                        self._swap_session_manager(result.new_session_manager)
                    if (
                        result.compact_done
                        and self._first_message
                        and self._session_manager
                        and not self._session_manager.user_named
                    ):
                        asyncio.create_task(self._auto_name_session(self._first_message))
                    if result.exit:
                        from .output import SessionEnd
                        await self.frontend.render(SessionEnd())
                        break
                    if result.agent_message is not None:
                        msg = result.agent_message
                        if self._session_manager is not None:
                            self._session_manager.record_user(msg)
                        if self._first_message is None:
                            self._first_message = user_input  # short form for session naming
                            if self._session_manager is not None and not self._session_manager.user_named:
                                asyncio.create_task(self._auto_name_session(user_input))
                        await self._agent_turn(msg)
                    continue

                # Bang prefix
                if user_input.startswith("!"):
                    await self._handle_bang(user_input)
                    continue

                # Regular message → record and send to agent
                if self._session_manager is not None:
                    self._session_manager.record_user(user_input)

                # Auto-name the session from the first user message
                if self._first_message is None:
                    self._first_message = user_input
                    if self._session_manager is not None and not self._session_manager.user_named:
                        asyncio.create_task(self._auto_name_session(user_input))

                await self._agent_turn(user_input)

        except (KeyboardInterrupt, EOFError):
            from .output import TextOutput

            await self.frontend.render(
                TextOutput("Interrupted by the user. Exiting TUI...", "warning")
            )
        finally:
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

    # ------------------------------------------------------------------
    # Agent turn
    # ------------------------------------------------------------------

    async def _agent_turn(self, user_input: str) -> None:
        import os
        import select
        import sys
        import threading

        from prompt_toolkit.input.vt100_parser import Vt100Parser
        from prompt_toolkit.keys import Keys

        from .output import TextOutput

        # Clear previous streaming state before new turn
        self._clear_streaming_state()

        await self.frontend.start_thinking()

        task = asyncio.create_task(self.agent.respond(user_input))  # type: ignore[union-attr]

        # --- Interrupt detection (Escape + Ctrl+C) ---
        _interrupted = threading.Event()
        _ctrl_c_count = 0
        _force_exit = False

        def _on_key(key_press) -> None:
            """Called by Vt100Parser for each decoded key."""
            nonlocal _ctrl_c_count, _force_exit
            key = key_press.key
            if key == Keys.ControlC:
                _ctrl_c_count += 1
                _interrupted.set()
                if _ctrl_c_count >= 2:
                    _force_exit = True
                    sys.stderr.write("\r\x1b[2mExiting…\x1b[0m\n")
                    sys.stderr.flush()
                else:
                    sys.stderr.write(
                        "\r\x1b[2mInterrupting agent… (press Ctrl+C again to exit)\x1b[0m\n"
                    )
                    sys.stderr.flush()
            elif key == Keys.Escape:
                _interrupted.set()
                sys.stderr.write("\r\x1b[2mInterrupting agent…\x1b[0m\n")
                sys.stderr.flush()

        parser = Vt100Parser(_on_key)

        fd = sys.stdin.fileno() if sys.stdin.isatty() else -1
        _stop_reader = threading.Event()
        _raw_ctx = None

        if fd >= 0:
            try:
                from prompt_toolkit.input.vt100 import raw_mode

                _raw_ctx = raw_mode(fd)
                _raw_ctx.__enter__()

                def _stdin_reader_thread() -> None:
                    while not _stop_reader.is_set():
                        try:
                            rlist, _, _ = select.select([fd], [], [], 0.1)
                            if not rlist:
                                continue
                            data = os.read(fd, 1024)
                        except OSError:
                            if _stop_reader.is_set():
                                break
                            continue
                        if not data:
                            break
                        # Treat a bare ESC byte as an immediate interrupt rather
                        # than feeding it to the parser, which buffers it waiting
                        # for a possible escape sequence (requiring two presses).
                        if data == b"\x1b":
                            _interrupted.set()
                            sys.stderr.write("\r\x1b[2mInterrupting agent…\x1b[0m\n")
                            sys.stderr.flush()
                            continue
                        parser.feed(data.decode("utf-8", errors="ignore"))

                threading.Thread(target=_stdin_reader_thread, daemon=True).start()
            except Exception:
                if _raw_ctx is not None:
                    try:
                        _raw_ctx.__exit__(None, None, None)
                    except Exception:
                        pass
                    _raw_ctx = None

        # Poll the threading.Event + frontend disconnect.
        async def _poll_interrupt() -> None:
            while not task.done():
                if _interrupted.is_set():
                    return
                if not self.frontend.is_connected:
                    _interrupted.set()
                    return
                await asyncio.sleep(0.1)

        poll_task = asyncio.create_task(_poll_interrupt())

        try:
            done, _pending = await asyncio.wait(
                {task, poll_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        if _interrupted.is_set():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass
            if not _force_exit:
                await self.frontend.render(TextOutput("Agent interrupted.", "warning"))
        elif task.done():
            exc = task.exception()
            if exc is not None:
                await self.frontend.render(TextOutput(f"Agent error: {exc}", "error"))

        # --- Cleanup ---
        await self.frontend.stop_thinking()

        _stop_reader.set()
        if _raw_ctx is not None:
            try:
                _raw_ctx.__exit__(None, None, None)
            except Exception:
                pass

        if _force_exit:
            raise KeyboardInterrupt
