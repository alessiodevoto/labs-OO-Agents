# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frontend protocol and TerminalFrontend implementation.

A ``Frontend`` is a **pure rendering surface** — it renders Output objects,
reads user input, and shows/hides a thinking indicator.  It has NO behavior:
no event subscription, no show_python decisions, no streaming state.

All behavior lives in ``Session``.
"""

import asyncio
import sys
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .output import (
    ActivityLine,
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
from .queue_state import QueueState
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

    async def typeahead_loop(self, state: QueueState) -> None:
        """Run a stay-open prompt with a dynamic prefix driven by ``state``.

        Returns when ``exit_typeahead()`` is called. Raises ``KeyboardInterrupt``
        on Ctrl+C and ``EOFError`` on Ctrl+D.
        """
        ...

    def exit_typeahead(self) -> None:
        """Signal the active typeahead loop to exit (agent finished)."""
        ...

    def invalidate_typeahead(self) -> None:
        """Force a redraw of the typeahead prompt (used by spinner tick)."""
        ...

    async def emit_user_message_above_prompt(self, content: str) -> None:
        """Commit a queued user message to scrollback above the active prompt."""
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
        self.patch_stdout_active = False  # set by session during _agent_turn

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
        if isinstance(output, TextOutput):
            self._render_text(output)
        elif isinstance(output, TableOutput):
            self._render_table(output)
        elif isinstance(output, HelpOutput):
            self._console.print_help(output.commands)
        elif isinstance(output, AgentMessage):
            if self.patch_stdout_active:
                await self._print_agent_via_run_in_terminal(
                    output.content, show_rule=output.show_rule
                )
            else:
                self._console.print_agent(output.content, show_rule=output.show_rule)
        elif isinstance(output, CodeExecution):
            self._render_code_execution(output)
        elif isinstance(output, StartupInfo):
            self._render_startup(output)
        elif isinstance(output, ClearScreen):
            self._console.console.clear()
        elif isinstance(output, Thinking):
            if output.active:
                self._console.start_spinner(output.message)
            else:
                self._console.stop_spinner()
        elif isinstance(output, BashOutput):
            self._render_bash(output)
        elif isinstance(output, RichOutput):
            self._render_rich(output)
        elif isinstance(output, DiffOutput):
            self._render_diff(output)
        elif isinstance(output, HistoryReplay):
            self._render_history_replay(output)
        elif isinstance(output, ActivityLine):
            self._render_activity_line(output)
        elif isinstance(output, UserMessage):
            self._render_user_message(output)

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
            result = await self._input_handler.get_input(
                prompt, default=default, bottom_toolbar=bottom_toolbar
            )
            if result.strip() and not self.patch_stdout_active:
                self._overwrite_input(prompt, result)
            return result

        # Fallback: plain input via executor
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._console.console.input(f"[user]{prompt}[/user]").strip()
        )

    async def start_thinking(self, message: str = "thinking...") -> None:
        self._console.start_spinner(message)

    async def stop_thinking(self) -> None:
        self._console.stop_spinner()

    async def typeahead_loop(self, state: QueueState) -> None:
        """Run the stay-open typeahead prompt until exit_typeahead() is called."""
        if self._input_handler is None:
            # No interactive handler (tests without init_input, or headless) —
            # nothing to do; just return so the caller can await the agent.
            return
        await self._input_handler.typeahead_loop(state)

    def exit_typeahead(self) -> None:
        if self._input_handler is not None:
            self._input_handler.exit_typeahead()

    def invalidate_typeahead(self) -> None:
        if self._input_handler is not None:
            self._input_handler.invalidate()

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _overwrite_input(self, prompt: str, text: str) -> None:
        """Replace prompt_toolkit's input line(s) with a styled version."""
        import math
        import sys

        from rich.padding import Padding
        from rich.text import Text

        c = self._console.console
        width = c.size.width or 80
        # Count visual rows: each logical line may wrap across multiple terminal rows.
        # The first line includes the prompt prefix (e.g. "❯ ").
        lines = text.split("\n")
        visual_rows = 0
        for i, line in enumerate(lines):
            display_len = len(line) + (len(prompt) if i == 0 else 0)
            visual_rows += max(1, math.ceil(display_len / width))
        # Move cursor up and clear each visual row
        for _ in range(visual_rows):
            sys.stdout.write("\033[A\033[2K")
        sys.stdout.flush()
        # Padding with background style — fills full width, no border
        content = Text(text, style=f"{COLORS['rosewater']} on {COLORS['surface0']}")
        padded = Padding(content, (0, 1), style=f"on {COLORS['surface0']}", expand=True)
        c.print(padded)

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
        if output.stdout:
            self._console.console.print(output.stdout, end="")
            if not output.stdout.endswith("\n"):
                self._console.console.print()
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

        suffix = Path(filename).suffix or ".txt"
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vi"))

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            os.close(tmp_fd)
            Path(tmp_path).write_text(content)
            self._console.stop_spinner()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: subprocess.run([editor, tmp_path]))
            return Path(tmp_path).read_text(errors="replace")
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def _emit_ansi_above_prompt(self, rendered: str) -> None:
        """Write a pre-rendered ANSI string above the running typeahead prompt.

        Uses prompt_toolkit's ``run_in_terminal`` when a prompt is active
        (pauses the prompt, writes to the real stdout, resumes). Falls back
        to a direct write when no prompt is running.
        """
        import sys as _sys

        from prompt_toolkit.application import get_app_or_none, run_in_terminal

        if not rendered:
            return

        def _emit() -> None:
            stream = _sys.__stdout__ or _sys.stdout
            stream.write(rendered)
            stream.flush()

        app = get_app_or_none()
        if app is None:
            _emit()
        else:
            await run_in_terminal(_emit)

    async def _print_agent_via_run_in_terminal(self, content: str, *, show_rule: bool) -> None:
        """Render an AgentMessage above the running typeahead prompt.

        Pre-renders Markdown to an ANSI string via a throwaway Rich Console,
        then writes it to the real stdout inside ``run_in_terminal``.
        """
        import io
        import textwrap

        from rich.console import Console as _Console
        from rich.markdown import Markdown as _Markdown
        from rich.rule import Rule as _Rule

        cleaned = content.replace("\u00a0", " ")
        cleaned = textwrap.dedent(cleaned).strip()

        width = self._console.console.size.width or 100
        buf = io.StringIO()
        c = _Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=width,
        )
        if show_rule:
            c.print(_Rule(title="[bold]OO[/bold]", style=COLORS["surface2"], align="left"))
        c.print(_Markdown(cleaned))
        await self._emit_ansi_above_prompt(buf.getvalue())

    async def emit_user_message_above_prompt(self, content: str) -> None:
        """Render a queued user message as a scrollback bar above the prompt.

        Used by the session right before queued type-ahead messages are
        delivered to the agent. Without this the user sees no record of
        what they sent — the dynamic │ lines get erased on prompt exit and
        were never committed to scrollback.

        Styled to match ``_overwrite_input`` (rosewater text on surface0)
        so a queued message looks identical to a first/between-turn
        message once it lands in scrollback.
        """
        import io

        from rich.console import Console as _Console
        from rich.padding import Padding as _Padding
        from rich.text import Text as _Text

        width = self._console.console.size.width or 100
        for line in content.split("\n"):
            styled = _Text(line, style=f"{COLORS['rosewater']} on {COLORS['surface0']}")
            padded = _Padding(styled, (0, 1), style=f"on {COLORS['surface0']}", expand=True)
            buf = io.StringIO()
            _Console(file=buf, force_terminal=True, color_system="truecolor", width=width).print(
                padded
            )
            await self._emit_ansi_above_prompt(buf.getvalue())

    def _render_activity_line(self, output: ActivityLine) -> None:
        """Render a live activity preview line (reasoning or code).

        Same styling in both paths (patch_stdout active or not): reasoning is
        dim italic overlay, code is dim subtext with the first-line comment
        highlighted in normal text colour. Under patch_stdout we render Rich
        to a string and print it so prompt_toolkit routes it above the prompt.
        """
        from rich.text import Text

        if output.kind == "reasoning":
            styled = Text(output.content, style=f"dim italic {COLORS['overlay1']}")
        else:
            styled = Text(f"● {output.content}", style=f"dim {COLORS['subtext0']}")
            first_line = output.content.split("\n", 1)[0]
            if first_line.lstrip().startswith("#"):
                styled.stylize(f"not dim {COLORS['text']}", 0, 2 + len(first_line))

        if self.patch_stdout_active:
            import io

            from rich.console import Console as _Console

            width = self._console.console.size.width or 100
            buf = io.StringIO()
            _Console(file=buf, force_terminal=True, color_system="truecolor", width=width).print(
                styled
            )
            rendered = buf.getvalue()
            if rendered:
                print(rendered, end="", flush=True)
        else:
            self._console.console.print(styled)

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
        """Render past conversation turns in a dimmed style."""
        from rich.markdown import Markdown
        from rich.rule import Rule
        from rich.text import Text

        dim = COLORS["overlay1"]
        user_color = COLORS["subtext1"]
        c = self._console.console

        if output.show_header:
            c.print(
                Rule(
                    title=f"[{dim}]session {output.session_id} — history[/]",
                    style=COLORS["surface1"],
                )
            )
        for turn in output.turns:
            if turn.role == "user":
                c.print(
                    Text(f" You: {turn.content}", style=f"{user_color} on {COLORS['surface0']}")
                )
            else:
                # Render agent turns as dimmed markdown
                c.print(Text("OO:", style=f"bold {dim}"))
                c.print(Markdown(turn.content), style=dim)
            c.print()
        if output.show_footer:
            c.print(Rule(style=COLORS["surface1"]))

    def _render_startup(self, info: StartupInfo) -> None:
        from rich.rule import Rule
        from rich.table import Table

        green = COLORS["green"]
        subtext = COLORS["subtext1"]
        overlay = COLORS["overlay1"]
        sapphire = COLORS["sapphire"]
        peach = COLORS["peach"]
        yellow = COLORS["yellow"]

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

        if info.sandbox_available is not None:
            if info.sandbox_available:
                sandbox_text = f"[{green}]available[/] — /sandbox to toggle"
            else:
                sandbox_text = f"[{yellow}]not available[/] — bash runs unsandboxed"
            table.add_row("sandbox", sandbox_text)

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
        table.add_row("!python", f"[{overlay}]drop into IPython with agent in scope[/]")

        self._console.console.print(table)
        self._console.console.print()

    def _render_code_execution(self, execution: CodeExecution) -> None:
        """Render a code execution panel (reasoning + code + output)."""

        from rich.rule import Rule
        from rich.syntax import Syntax
        from rich.text import Text

        try:
            from context_blocks import ResultStatus as _RS

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
