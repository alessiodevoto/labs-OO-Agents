# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NeMo OO Agents TUI - A beautiful terminal interface for NeMo OO Agents.

Licensed to vibe.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

from pathlib import Path as _Path

from .agent import BaseTUIAgent, TUIAgent
from .config import AgentConfig, Config, SummarizationConfig, TUIConfig
from .console import TUIConsole
from .input_handler import TUIInputHandler
from .theme import CATPPUCCIN_THEME, COLORS


def _get_sw_skills_dir() -> str:
    """Entry-point hook — returns the packaged ``skills-sw/`` path.

    Registered under ``nemo_oo_tui.skills_dirs`` in pyproject.toml so
    ``Config.load`` picks it up automatically. Ships the SWE workflow
    skill set (brainstorm / root-cause / tdd / review / ship).
    """
    return str(_Path(__file__).parent / "skills-sw")


__all__ = [
    "AgentConfig",
    "BaseTUIAgent",
    "Config",
    "SummarizationConfig",
    "TUIAgent",
    "TUIConfig",
    "TUIConsole",
    "TUIInputHandler",
    "CATPPUCCIN_THEME",
    "COLORS",
]
