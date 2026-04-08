# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rich console wrapper with spinners and styled output.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .theme import CATPPUCCIN_THEME, COLORS

if TYPE_CHECKING:
    from .commands import CommandRegistry


class TUIConsole:
    """Rich console wrapper with Catppuccin styling for NeMo OO Agents TUI."""

    def __init__(self) -> None:
        self.console = Console(theme=CATPPUCCIN_THEME)
        self._input_handler = None
        self._live_spinner: Live | None = None

    def init_input_handler(self, registry: "CommandRegistry") -> None:
        """Initialize the input handler with history and completion.

        Args:
            registry: CommandRegistry instance to get completion options from
        """
        from .input_handler import TUIInputHandler

        self._input_handler = TUIInputHandler(registry=registry)

    def start_spinner(self, message: str = "NeMo OO Agents is thinking...") -> None:
        """Start the thinking spinner.

        Can be stopped with stop_spinner() or by the streaming display
        when first output arrives.
        """
        if self._live_spinner is not None:
            return  # Already running

        spinner = Spinner(
            "dots",
            text=Text(message, style=f"{COLORS['subtext1']}"),
            style=COLORS["mauve"],
        )
        self._live_spinner = Live(spinner, console=self.console, refresh_per_second=10)
        self._live_spinner.start()

    def stop_spinner(self) -> None:
        """Stop the thinking spinner if running."""
        if self._live_spinner is not None:
            self._live_spinner.stop()
            self._live_spinner = None

    @contextmanager
    def thinking_spinner(self) -> Generator[Live, None, None]:
        """Context manager for showing thinking spinner.

        Usage:
            with console.thinking_spinner():
                response = await agent.respond(message)
        """
        spinner = Spinner(
            "dots",
            text=Text("NeMo OO Agents is thinking...", style=f"{COLORS['subtext1']}"),
            style=COLORS["mauve"],
        )
        with Live(spinner, console=self.console, refresh_per_second=10) as live:
            yield live

    def print_user(self, message: str) -> None:
        """Print a user message."""
        self.console.print(f"[user]You:[/user] {message}")

    def print_agent(self, message: str) -> None:
        """Print an agent response in a styled panel."""
        import textwrap

        # Aggressive cleanup for markdown rendering:
        # 1. Replace any non-breaking spaces with regular spaces
        cleaned = message.replace("\u00a0", " ").replace("\xa0", " ")
        # 2. Dedent and strip
        cleaned = textwrap.dedent(cleaned).strip()
        # 3. Remove common leading whitespace from all lines
        lines = cleaned.split("\n")
        non_empty_lines = [line for line in lines if line.strip()]
        if non_empty_lines:
            min_indent = min(len(line) - len(line.lstrip()) for line in non_empty_lines)
            if min_indent > 0:
                lines = [line[min_indent:] if len(line) >= min_indent else line for line in lines]
        # 4. Normalize line endings and rejoin
        cleaned = "\n".join(line.rstrip() for line in lines)

        self.console.print(
            Panel(
                Markdown(cleaned),
                title="[agent]NeMo OO Agents[/agent]",
                border_style=COLORS["mauve"],
                padding=(0, 1),
            )
        )

    def print_error(self, message: str) -> None:
        """Print an error message."""
        self.console.print(f"[error]Error:[/error] {message}")

    def print_warning(self, message: str) -> None:
        """Print a warning message."""
        self.console.print(f"[warning]Warning:[/warning] {message}")

    def print_status(self, message: str) -> None:
        """Print a dim status message."""
        self.console.print(f"[status]{message}[/status]")

    def print_success(self, message: str) -> None:
        """Print a success message."""
        self.console.print(f"[success]✓[/success] {message}")

    def print_info(self, message: str) -> None:
        """Print an info message."""
        self.console.print(f"[info]ℹ[/info] {message}")

    def print_table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        """Print a formatted table with Catppuccin styling."""
        table = Table(
            title=title,
            title_style=f"bold {COLORS['lavender']}",
            border_style=COLORS["surface2"],
            header_style=f"bold {COLORS['lavender']}",
        )
        for col in columns:
            table.add_column(col, style=COLORS["text"])
        for row in rows:
            table.add_row(*row)
        self.console.print(table)

    def print_help(self, commands: dict[str, str]) -> None:
        """Print help for available commands."""
        self.console.print(f"\n[bold {COLORS['mauve']}]Available Commands:[/]\n")
        for cmd, desc in commands.items():
            self.console.print(
                f"  [bold {COLORS['sapphire']}]{cmd}[/] - [{COLORS['subtext1']}]{desc}[/]"
            )
        self.console.print()

    async def get_input(self, prompt: str = "You: ") -> str:
        """Get input from user with history and completion.

        Features (when input handler is initialized):
        - Up/Down arrows: Navigate history
        - Tab: Complete slash commands
        - Escape+Enter: Insert newline for multi-line input
        - Ctrl+R: Reverse history search
        """
        if self._input_handler:
            return await self._input_handler.get_input(prompt)
        # Fallback to basic Rich input (run in executor to not block)
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.console.input(f"[user]{prompt}[/user]").strip()
        )
