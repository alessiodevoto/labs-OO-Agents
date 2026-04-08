"""Catppuccin Mocha theme for Agent006 TUI.

Color palette from https://catppuccin.com/palette/
Using Mocha (darkest) variant for terminal aesthetics.
"""

from rich.theme import Theme

# Catppuccin Mocha color definitions
MOCHA = {
    # Accent colors
    "rosewater": "#f5e0dc",
    "flamingo": "#f2cdcd",
    "pink": "#f5c2e7",
    "mauve": "#cba6f7",
    "red": "#f38ba8",
    "maroon": "#eba0ac",
    "peach": "#fab387",
    "yellow": "#f9e2af",
    "green": "#a6e3a1",
    "teal": "#94e2d5",
    "sky": "#89dceb",
    "sapphire": "#74c7ec",
    "blue": "#89b4fa",
    "lavender": "#b4befe",
    # Text colors
    "text": "#cdd6f4",
    "subtext1": "#bac2de",
    "subtext0": "#a6adc8",
    # Overlay colors
    "overlay2": "#9399b2",
    "overlay1": "#7f849c",
    "overlay0": "#6c7086",
    # Surface colors
    "surface2": "#585b70",
    "surface1": "#45475a",
    "surface0": "#313244",
    # Base colors
    "base": "#1e1e2e",
    "mantle": "#181825",
    "crust": "#11111b",
}

# Rich theme using Catppuccin Mocha
CATPPUCCIN_THEME = Theme(
    {
        # Agent006 branding
        "agent006": f"bold {MOCHA['mauve']}",
        "tagline": f"italic {MOCHA['pink']}",
        # User interface
        "user": f"bold {MOCHA['green']}",
        "user.prompt": f"bold {MOCHA['green']}",
        "agent": f"bold {MOCHA['mauve']}",
        "agent.response": MOCHA["text"],
        # Status messages
        "status": MOCHA["overlay1"],
        "success": f"bold {MOCHA['green']}",
        "error": f"bold {MOCHA['red']}",
        "warning": f"bold {MOCHA['yellow']}",
        "info": f"bold {MOCHA['blue']}",
        # Panels and borders
        "panel.border": MOCHA["mauve"],
        "panel.title": f"bold {MOCHA['mauve']}",
        # Tables
        "table.header": f"bold {MOCHA['lavender']}",
        "table.border": MOCHA["surface2"],
        # Commands
        "command": f"bold {MOCHA['sapphire']}",
        "command.arg": MOCHA["sky"],
        # Spinners
        "spinner": MOCHA["mauve"],
        "spinner.text": MOCHA["subtext1"],
        # Code/technical
        "code": MOCHA["peach"],
        "path": MOCHA["teal"],
        "number": MOCHA["peach"],
        # History tags
        "tag": MOCHA["blue"],
        "tag.summary": MOCHA["yellow"],
        # MCP/Skills
        "mcp": MOCHA["sapphire"],
        "skill": MOCHA["pink"],
        "skill.active": f"bold {MOCHA['green']}",
    }
)

# Splash screen colors
SPLASH_TITLE = MOCHA["mauve"]
SPLASH_TAGLINE = MOCHA["pink"]
SPLASH_BORDER = MOCHA["lavender"]

# Export color values for direct use
COLORS = MOCHA
