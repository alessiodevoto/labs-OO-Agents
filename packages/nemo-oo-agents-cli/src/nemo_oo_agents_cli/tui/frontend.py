# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frontend protocol and TerminalFrontend implementation.

A ``Frontend`` is a **pure rendering surface** — it renders Output objects,
reads user input, and shows/hides a thinking indicator.  It has NO behavior:
no event subscription, no show_python decisions, no streaming state.

All behavior lives in ``Session``.
"""

import asyncio
import io
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .output import (
    AgentMessage,
    BashOutput,
    ClearScreen,
    CodeExecution,
    DiffOutput,
    HelpOutput,
    HistoryReplay,
    Output,
    RichOutput,
    StartupInfo,
    TableOutput,
    TextOutput,
    Thinking,
    UserMessage,
)
from .theme import COLORS

if TYPE_CHECKING:
    from .config import Config


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Frontend(Protocol):
    """Everything a Session needs from its rendering layer.

    Both ``TerminalFrontend`` and ``WebFrontend`` implement this protocol.
    Commands and Session code should ONLY interact with frontends through
    this protocol — never reach into frontend-specific attributes.

    Frontends are PURE RENDERING — no event subscription, no streaming state,
    no behavioral decisions.
    """

    async def render(self, output: Output) -> None:
        """Render one structured output object."""
        ...

    async def get_input(
        self,
        prompt: str,
        completions: list[str] | None = None,
        default: str = "",
        bottom_toolbar: object = None,
    ) -> str:
        """Read one line of user input.

        Args:
            prompt: The text to display before the cursor.
            completions: Optional list of completion candidates (e.g. model names).
                         When provided the frontend may offer autocomplete.
            default: Pre-fill the input buffer with this text.
            bottom_toolbar: Optional callable for a status bar (e.g. spinner).
        """
        ...

    async def start_thinking(self, message: str = "thinking...") -> None:
        """Show a loading/thinking indicator."""
        ...

    async def stop_thinking(self) -> None:
        """Hide the loading/thinking indicator."""
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the frontend is still connected (always True for terminal)."""
        ...

    async def open_editor(
        self, filename: str, content: str, language: str = "plaintext"
    ) -> str | None:
        """Open an editor for the given file content. Returns edited content or None."""
        ...

    def close(self) -> None:
        """Clean up frontend resources."""
        ...


# ---------------------------------------------------------------------------
# TerminalFrontend
# ---------------------------------------------------------------------------


class TerminalFrontend:
    """Frontend backed by Rich (output) + prompt_toolkit (input).

    Pure rendering — no event subscription or behavioral state.
    """

    def __init__(self, config: "Config") -> None:
        from .console import TUIConsole

        self._config = config
        self._console = TUIConsole()
        self._input_handler = None  # initialised after registry is ready
        self._renderers = self._build_renderer_map()

    # ------------------------------------------------------------------
    # Input handler initialisation (needs the command registry)
    # ------------------------------------------------------------------

    def init_input(self, registry) -> None:
        """Wire up the prompt_toolkit input handler with slash completions."""
        from .input_handler import TUIInputHandler

        self._input_handler = TUIInputHandler(
            registry=registry,
            vi_mode=self._config.tui.vi_mode,
        )

    @property
    def is_connected(self) -> bool:
        """Terminal is always connected."""
        return True

    def close(self) -> None:
        """Clean up terminal state."""
        pass

    # ------------------------------------------------------------------
    # Frontend protocol implementation
    # ------------------------------------------------------------------

    async def render(self, output: Output) -> None:  # type: ignore[override]
        """Dispatch *output* to the appropriate Rich rendering call."""
        handler = self._renderers.get(type(output))
        if handler is not None:
            handler(output)

    def _build_renderer_map(self) -> "dict[type, Callable[[Any], None]]":
        """Build the {Output subclass → handler} dispatch table once at
        construction time. Cheaper and clearer than an isinstance ladder;
        also surfaces unregistered Output kinds as silent no-ops (same
        behaviour as the old default-fallthrough)."""
        return {
            TextOutput: self._render_text,
            TableOutput: self._render_table,
            HelpOutput: self._render_help,
            AgentMessage: self._render_agent_message,
            CodeExecution: self._render_code_execution,
            StartupInfo: self._render_startup,
            ClearScreen: self._render_clear,
            Thinking: self._render_thinking,
            BashOutput: self._render_bash,
            RichOutput: self._render_rich,
            DiffOutput: self._render_diff,
            HistoryReplay: self._render_history_replay,
            UserMessage: self._render_user_message,
        }

    def _render_help(self, output: HelpOutput) -> None:
        self._console.print_help(output.commands)

    def _render_agent_message(self, output: AgentMessage) -> None:
        self._console.print_agent(output.content, show_rule=output.show_rule)

    def _render_clear(self, _output: ClearScreen) -> None:
        self._console.console.clear()

    def _render_thinking(self, output: Thinking) -> None:
        if output.active:
            self._console.start_spinner(output.message)
        else:
            self._console.stop_spinner()

    async def get_input(
        self,
        prompt: str,
        completions: list[str] | None = None,
        default: str = "",
        bottom_toolbar: object = None,
    ) -> str:
        """Read user input from the terminal.

        When *completions* are provided, a temporary PromptSession with
        WordCompleter is used (e.g. for ``/switch`` model selection).
        Otherwise the main session (with slash-command completion) is used.
        """
        if completions:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter

            session = PromptSession(
                completer=WordCompleter(completions, ignore_case=True),
                complete_while_typing=True,
            )
            return (await session.prompt_async(prompt)).strip()

        if self._input_handler:
            return await self._input_handler.get_input(
                prompt, default=default, bottom_toolbar=bottom_toolbar
            )

        # Fallback: plain input via executor
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._console.console.input(f"[user]{prompt}[/user]").strip()
        )

    async def start_thinking(self, message: str = "thinking...") -> None:
        self._console.start_spinner(message)

    async def stop_thinking(self) -> None:
        self._console.stop_spinner()

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _render_text(self, output: TextOutput) -> None:
        lvl = output.level
        if lvl == "error":
            self._console.print_error(output.content)
        elif lvl == "warning":
            self._console.print_warning(output.content)
        elif lvl == "success":
            self._console.print_success(output.content)
        elif lvl == "status":
            self._console.print_status(output.content)
        else:
            self._console.print_info(output.content)

    def _render_table(self, output: TableOutput) -> None:
        self._console.print_table(output.title, output.columns, output.rows)
        if output.footer:
            self._console.print_status(output.footer)

    def _render_bash(self, output: BashOutput) -> None:
        if output.command:
            self._console.console.print(f"[bold dark_orange]❯ {output.command}[/]")
        if output.stdout:
            # Ensure text ends with newline so _EmitStream produces exactly
            # ONE flush (one emit_block call). Two separate flushes (text
            # sans \n, then bare \n) create two run_in_terminal hops with
            # a prompt repaint between them that overwrites single-line
            # output. See issue #156.
            text = output.stdout if output.stdout.endswith("\n") else output.stdout + "\n"
            self._console.console.print(text, end="")
        if output.stderr:
            self._console.print_error(output.stderr)

    def _render_rich(self, output: RichOutput) -> None:
        """Terminal fallback for rich visual output — show summary text."""
        label = f"[bold]{output.kind}[/bold]"
        if output.title:
            label = f"[bold]{output.kind}: {output.title}[/bold]"
        if output.fallback_text:
            self._console.print_info(f"{label} — {output.fallback_text}")
        else:
            self._console.print_info(
                f"{label} (rich rendering available in web frontend — run `nemo_oo_agents web`)"
            )

    def _render_diff(self, output: DiffOutput) -> None:
        """Render a unified diff with syntax highlighting."""
        from rich.rule import Rule
        from rich.syntax import Syntax

        if not output.diff.strip():
            return
        label = f"diff — {output.filename}" if output.filename else "diff"
        self._console.console.print(
            Rule(title=f"[bold]{label}[/bold]", style=COLORS["surface2"], align="left")
        )
        self._console.console.print(Syntax(output.diff, "diff", theme="monokai", word_wrap=True))

    async def open_editor(
        self, filename: str, content: str, language: str = "plaintext"
    ) -> str | None:
        """Open *content* in $EDITOR via a temp file and return the edited text."""
        import os
        import subprocess
        import tempfile
        from pathlib import Path

        from prompt_toolkit.application import run_in_terminal

        suffix = Path(filename).suffix or ".txt"
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vi"))

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            os.close(tmp_fd)
            Path(tmp_path).write_text(content)
            self._console.stop_spinner()
            await run_in_terminal(lambda: subprocess.run([editor, tmp_path]), in_executor=True)
            return Path(tmp_path).read_text(errors="replace")
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _render_user_message(self, output: UserMessage) -> None:
        """Render the user's submitted text with a high-contrast background bar."""
        from rich.text import Text

        c = self._console.console
        # Full-width bar: white text on light grey background
        width = c.size.width
        # Pad to full terminal width so the background spans the whole line
        display = f" {output.content} "
        text = Text(display.ljust(width), style=f"{COLORS['text']} on {COLORS['surface2']}")
        c.print(text)

    def _render_history_replay(self, output: HistoryReplay) -> None:
        """Render past conversation turns in a dimmed style.

        Uses a buffered console to pre-render all output, then writes
        the result to the terminal in a single flush — eliminates the
        flicker that occurs when each turn triggers a separate write.
        """
        from rich.console import Console as RichConsole
        from rich.markdown import Markdown
        from rich.rule import Rule
        from rich.text import Text

        dim = COLORS["overlay1"]
        user_color = COLORS["subtext1"]

        # Pre-render into a string buffer so the terminal gets one
        # contiguous write instead of per-turn flushes.
        buf = io.StringIO()
        width = self._console.console.width or 80
        bc = RichConsole(file=buf, width=width, highlight=False, force_terminal=True)

        if output.show_header:
            omit_note = ""
            if output.omitted_count:
                omit_note = f" ({output.omitted_count} earlier turns omitted)"
            bc.print(
                Rule(
                    title=f"[{dim}]session {output.session_id} — history{omit_note}[/]",
                    style=COLORS["surface1"],
                )
            )
        for turn in output.turns:
            if turn.role == "user":
                bc.print(
                    Text(f" You: {turn.content}", style=f"{user_color} on {COLORS['surface0']}")
                )
            else:
                # Render agent turns as dimmed markdown
                bc.print(Text("OO:", style=f"bold {dim}"))
                bc.print(Markdown(turn.content), style=dim)
            bc.print()
        if output.show_footer:
            bc.print(Rule(style=COLORS["surface1"]))

        # Single write to the real terminal
        self._console.console.file.write(buf.getvalue())
        self._console.console.file.flush()

    def _render_startup(self, info: StartupInfo) -> None:
        from rich.rule import Rule
        from rich.table import Table

        green = COLORS["green"]
        subtext = COLORS["subtext1"]
        overlay = COLORS["overlay1"]
        sapphire = COLORS["sapphire"]
        peach = COLORS["peach"]

        self._console.console.print(
            Rule(
                title=f"[bold {COLORS['mauve']}]NeMo OO Agents ready[/]",
                style=COLORS["surface2"],
            )
        )

        table = Table.grid(padding=(0, 2))
        table.add_column(style=f"bold {sapphire}", no_wrap=True)
        table.add_column(style=subtext)

        table.add_row(
            "model",
            f"[bold {green}]{info.short_model}[/] [{overlay}]({info.model})[/]",
        )
        table.add_row("working dir", info.working_dir)

        if info.history_policy and info.history_limit:
            table.add_row(
                "history",
                f"{info.history_policy}  limit {info.history_limit:,} tokens",
            )

        if info.tracing_enabled:
            trace_val = (
                f"[{peach}]{info.trace_dir}[/]"
                if info.trace_dir
                else f"[{peach}]OTLP auto-probe[/]"
            )
            table.add_row("tracing", trace_val)

        if info.custom_agent:
            table.add_row("agent", f"[{green}]{info.custom_agent}[/]")

        keybinds = ("vi mode  " if info.vi_mode else "") + (
            "Tab: complete  |  ↑↓: history  |  Shift+Enter: newline  |  Esc: interrupt  |  Ctrl+C ×2: exit"
        )
        table.add_row("keys", f"[{overlay}]{keybinds}[/]")
        table.add_row(
            "commands",
            f"[{sapphire}]/help[/][{overlay}] for all  ·  [/][{sapphire}]/exit[/][{overlay}] to quit[/]",
        )
        table.add_row("!cmd", f"[{overlay}]run a bash command (e.g. !ls -la)[/]")

        self._console.console.print(table)
        self._console.console.print()

    def _render_code_execution(self, execution: CodeExecution) -> None:
        """Render a code execution panel (reasoning + code + output)."""

        from rich.rule import Rule
        from rich.syntax import Syntax
        from rich.text import Text

        try:
            from nemo_oo_agents.context_blocks import ResultStatus as _RS

            _NO_RETURN = None
            try:
                from nemo_oo_agents.events import (
                    _NO_RETURN as _NO_RETURN,  # type: ignore[assignment]
                )
            except ImportError:
                pass
        except ImportError:
            _RS = None
            _NO_RETURN = None

        self._console.stop_spinner()

        elements: list = []

        if execution.code:
            elements.append(
                Syntax(
                    execution.code.strip(),
                    "python",
                    theme="monokai",
                    line_numbers=True,
                    word_wrap=True,
                )
            )

        output_parts: list = []
        if execution.stdout:
            output_parts.append(Text(execution.stdout, style=COLORS["text"]))
        if execution.stderr:
            output_parts.append(Text(execution.stderr, style=COLORS["peach"]))
        if execution.error:
            output_parts.append(Text(execution.error, style=f"bold {COLORS['red']}"))
        if execution.value is not None:
            if _NO_RETURN is None or execution.value is not _NO_RETURN:
                val = execution.value if isinstance(execution.value, str) else repr(execution.value)
                output_parts.append(Text(f"=> {val}", style=f"bold {COLORS['green']}"))

        if output_parts:
            elements.append(Text(""))
            elements.append(Rule(style=COLORS["surface1"]))
            for part in output_parts:
                elements.append(part)

        if elements:
            self._console.console.print(
                Rule(title="[python]Python[/python]", style=COLORS["surface2"], align="left")
            )
            for el in elements:
                self._console.console.print(el)
            sys.stdout.flush()
            sys.stderr.flush()

        self._console.start_spinner()

    # ------------------------------------------------------------------
    # Pass-through accessors (used by Session)
    # ------------------------------------------------------------------

    @property
    def console(self):
        return self._console

    @property
    def raw_console(self):
        return self._console.console
