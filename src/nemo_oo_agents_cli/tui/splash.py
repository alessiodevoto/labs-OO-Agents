# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Splash screen with ASCII art for NeMo OO Agents TUI.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

import time

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from .theme import SPLASH_BORDER, SPLASH_TAGLINE, SPLASH_TITLE

AGENT006_ASCII = r"""               _   __     __  ___
              / | / /__  /  |/  /___
             /  |/ / _ \/ /|_/ / __ \
            / /|  /  __/ /  / / /_/ /
           /_/ |_/\___/_/  /_/\____/
   ____  ____     ___                    __
  / __ \/ __ \   /   | ____ ____  ____  / /______
 / / / / / / /  / /| |/ __ `/ _ \/ __ \/ __/ ___/
/ /_/ / /_/ /  / ___ / /_/ /  __/ / / / /_(__  )
\____/\____/  /_/  |_\__, /\___/_/ /_/\__/____/
                    /____/"""


def show_splash(console: Console, delay: float = 0.8) -> None:
    """Display the NeMo OO Agents splash screen.

    The splash stays on screen and scrolls off naturally as the user chats.

    Args:
        console: Rich console instance
        delay: How long to pause after showing splash (seconds)
    """
    title = Text(AGENT006_ASCII, style=f"bold {SPLASH_TITLE}")
    tagline = Align.center(Text("licensed to vibe", style=f"italic {SPLASH_TAGLINE}"))

    panel = Panel(
        Group(title, tagline),
        border_style=SPLASH_BORDER,
        padding=(0, 2),
    )

    console.print(Align.center(panel))
    time.sleep(delay)
