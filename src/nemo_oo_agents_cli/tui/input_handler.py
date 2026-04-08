"""Input handling with history, completion, and multi-line support.

Uses prompt_toolkit for readline-like input experience.
"""

from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .theme import COLORS

if TYPE_CHECKING:
    from .commands import CommandRegistry


class SlashCommandCompleter(Completer):
    """Completer for slash commands with descriptions."""

    def __init__(self, registry: "CommandRegistry"):
        """Initialize completer with command registry.

        Args:
            registry: CommandRegistry instance to get completion options from
        """
        # Get completion options from registry
        self.commands = registry.get_completions()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Only complete if starting with /
        if not text.startswith("/"):
            return

        # Find matching commands
        for cmd, desc in self.commands.items():
            if cmd.lower().startswith(text.lower()):
                # Yield the remaining part of the command
                yield Completion(
                    cmd[len(text) :],
                    start_position=0,
                    display=cmd,
                    display_meta=desc,
                )


def create_key_bindings() -> KeyBindings:
    """Create key bindings for multi-line input and slash command completion.

    - Enter: Submit
    - Alt+Enter or Option+Enter: Insert newline for multi-line input
    - Any character after /: Keep completion menu open

    Note: Most terminals send Alt+Enter as Escape followed by Enter.
    """
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        """Alt+Enter (Option+Enter on Mac) inserts a newline for multi-line input."""
        event.current_buffer.insert_text("\n")

    # Trigger and maintain completion for slash commands
    def _handle_char_for_completion(event, char: str):
        """Insert character and trigger completion if in a slash command."""
        buffer = event.current_buffer
        buffer.insert_text(char)

        # Check if we're typing a slash command
        text = buffer.text[: buffer.cursor_position]
        if text.startswith("/") or (("\n/" in text) and text.rsplit("\n", 1)[-1].startswith("/")):
            buffer.start_completion(select_first=False)

    # Bind all alphanumeric keys to maintain completion
    for char in "abcdefghijklmnopqrstuvwxyz":

        @bindings.add(char)
        def _(event, c=char):
            _handle_char_for_completion(event, c)

    @bindings.add("/")
    def _(event):
        _handle_char_for_completion(event, "/")

    @bindings.add(" ")
    def _(event):
        _handle_char_for_completion(event, " ")

    @bindings.add("c-h")  # Backspace
    @bindings.add("backspace")
    def _(event):
        """Handle backspace and re-trigger completion if still in slash command."""
        buffer = event.current_buffer
        buffer.delete_before_cursor(1)

        # Check if we're still in a slash command
        text = buffer.text[: buffer.cursor_position]
        if text.startswith("/"):
            buffer.start_completion(select_first=False)

    return bindings


def create_prompt_style() -> Style:
    """Create prompt_toolkit style using Catppuccin colors."""
    return Style.from_dict(
        {
            # Prompt
            "prompt": COLORS["green"],
            "prompt.continuation": COLORS["overlay1"],
            # Completion menu
            "completion-menu": f"bg:{COLORS['surface0']} {COLORS['text']}",
            "completion-menu.completion": f"bg:{COLORS['surface0']} {COLORS['text']}",
            "completion-menu.completion.current": f"bg:{COLORS['surface2']} {COLORS['mauve']}",
            "completion-menu.meta": COLORS["overlay1"],
            "completion-menu.meta.current": COLORS["lavender"],
            # Scrollbar
            "scrollbar.background": COLORS["surface0"],
            "scrollbar.button": COLORS["surface2"],
        }
    )


class TUIInputHandler:
    """Handle user input with history, completion, and multi-line support."""

    def __init__(self, registry: "CommandRegistry"):
        """Initialize the input handler.

        Args:
            registry: CommandRegistry instance to get completion options from
        """
        from prompt_toolkit.shortcuts import CompleteStyle

        self.history = InMemoryHistory()
        self.completer = SlashCommandCompleter(registry)
        self.key_bindings = create_key_bindings()
        self.style = create_prompt_style()

        self.session = PromptSession(
            history=self.history,
            completer=self.completer,
            key_bindings=self.key_bindings,
            style=self.style,
            multiline=False,  # Enter submits by default
            complete_while_typing=False,  # We handle it manually via key bindings
            complete_style=CompleteStyle.COLUMN,  # Single column with descriptions
            enable_history_search=True,  # Ctrl+R for reverse search
        )

    async def get_input(self, prompt: str = "You: ") -> str:
        """Get input from user with history and completion.

        Args:
            prompt: The prompt to display

        Returns:
            User input string (may be multi-line)

        Raises:
            EOFError: If Ctrl+D is pressed
            KeyboardInterrupt: If Ctrl+C is pressed
        """
        # Use async prompt to work within existing event loop
        result = await self.session.prompt_async(
            [("class:prompt", prompt)],
            multiline=False,
        )
        return result.strip()

    async def get_multiline_input(self, prompt: str = "You: ") -> str:
        """Get multi-line input (submit with Escape+Enter twice or empty line).

        Args:
            prompt: The prompt to display

        Returns:
            Multi-line user input string
        """
        result = await self.session.prompt_async(
            [("class:prompt", prompt)],
            multiline=True,
            prompt_continuation=[("class:prompt.continuation", "... ")],
        )
        return result.strip()
