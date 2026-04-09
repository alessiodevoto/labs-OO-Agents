# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rich console wrapper with spinners and styled output.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .theme import CATPPUCCIN_THEME, COLORS


class TUIConsole:
    """Rich console wrapper with Catppuccin styling for NeMo OO Agents TUI."""

    def __init__(self) -> None:
        self.console = Console(theme=CATPPUCCIN_THEME)
        self._live_spinner: Live | None = None

    def start_spinner(self, message: str = "thinking...") -> None:
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

    def print_agent(self, message: str) -> None:
        """Print an agent response with a rule header."""
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
            Rule(title="[agent]NeMo OO Agents[/agent]", style=COLORS["surface2"], align="left")
        )
        self.console.print(Markdown(cleaned))

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
        self.console.print(f"[success]\u2713[/success] {message}")

    def print_info(self, message: str) -> None:
        """Print an info message."""
        self.console.print(f"[info]\u2139[/info] {message}")

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
