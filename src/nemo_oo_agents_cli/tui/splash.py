"""Splash screen with ASCII art for NeMo OO Agents TUI.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

import time

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .theme import SPLASH_BORDER, SPLASH_TAGLINE, SPLASH_TITLE

AGENT006_ASCII = r"""
    _                    _    ___   ___   __
   / \   __ _  ___ _ __ | |_ / _ \ / _ \ / /_
  / _ \ / _` |/ _ \ '_ \| __| | | | | | | '_ \
 / ___ \ (_| |  __/ | | | |_| |_| | |_| | (_) |
/_/   \_\__, |\___|_| |_|\__|\___/ \___/ \___/
        |___/
"""


def show_splash(console: Console, delay: float = 0.8) -> None:
    """Display the NeMo OO Agents splash screen.

    The splash stays on screen and scrolls off naturally as the user chats.

    Args:
        console: Rich console instance
        delay: How long to pause after showing splash (seconds)
    """
    # Build styled content with Catppuccin colors
    title = Text(AGENT006_ASCII, style=f"bold {SPLASH_TITLE}")
    tagline = Text("\n           licensed to vibe", style=f"italic {SPLASH_TAGLINE}")

    # Create panel with Catppuccin border
    panel = Panel(
        title + tagline,
        border_style=SPLASH_BORDER,
        padding=(1, 4),
    )

    # Print centered panel (will scroll off naturally as chat continues)
    console.print(Align.center(panel))
    console.print()  # Add spacing after splash

    time.sleep(delay)
