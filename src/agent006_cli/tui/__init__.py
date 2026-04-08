"""Agent006 TUI - A beautiful terminal interface for Agent006.

Licensed to vibe.

Uses Catppuccin Mocha theme from https://catppuccin.com/palette/
"""

from .agent import TUIAgent
from .config import AgentConfig, Config, SummarizationConfig, TUIConfig
from .console import TUIConsole
from .input_handler import TUIInputHandler
from .streaming_display import StreamingDisplay
from .theme import CATPPUCCIN_THEME, COLORS

__all__ = [
    "AgentConfig",
    "Config",
    "SummarizationConfig",
    "TUIAgent",
    "TUIConfig",
    "TUIConsole",
    "TUIInputHandler",
    "StreamingDisplay",
    "CATPPUCCIN_THEME",
    "COLORS",
]
