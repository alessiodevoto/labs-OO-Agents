# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Input handling with history, completion, and multi-line support.

Uses prompt_toolkit for readline-like input experience.
The actual completion logic lives in ``completer.py`` (shared with the web
frontend); this module is just the prompt_toolkit adapter.
"""

from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import Completer as PtCompleter
from prompt_toolkit.completion import Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .completer import Completer
from .queue_state import QueueState
from .theme import COLORS

if TYPE_CHECKING:
    from .commands import CommandRegistry


class SlashCommandCompleter(PtCompleter):
    """prompt_toolkit adapter over the shared ``Completer`` engine."""

    def __init__(self, registry: "CommandRegistry"):
        self._completer = Completer(registry=registry)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") and not text.startswith("!"):
            return

        for item in self._completer.complete(text):
            # Calculate how much text to replace: the item.text is the full
            # replacement, so we insert the suffix after what's already typed.
            suffix = item.text[len(text) :] if item.text.startswith(text) else item.text
            if not suffix and item.text == text:
                continue  # exact match, nothing to insert
            yield Completion(
                suffix if item.text.startswith(text) else item.text,
                start_position=0 if item.text.startswith(text) else -len(text),
                display=item.display,
                display_meta=item.description,
            )


def create_key_bindings(vi_mode: bool = False) -> KeyBindings:
    """Create key bindings for multi-line input and slash command completion.

    - Enter: Submit
    - Alt+Enter or Option+Enter: Insert newline for multi-line input
    - Shift+Enter: Insert newline (requires iTerm2/kitty/WezTerm with CSI u mode)
    - Any character after /: Keep completion menu open (disabled in vi mode to
      avoid clashing with vi normal-mode movement keys)

    Note: Most terminals send Alt+Enter as Escape followed by Enter.
    Shift+Enter works in terminals that send \\x1b[13;2u (Kitty keyboard protocol).

    Args:
        vi_mode: When True, skip letter-key completion bindings that would
                 conflict with vi normal-mode commands.
    """
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event):
        """Enter submits the current input."""
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    def _(event):
        """Shift+Enter on iTerm2 sends \\n (ControlJ) — insert a newline."""
        event.current_buffer.insert_text("\n")

    @bindings.add("escape", "enter")
    def _(event):
        """Alt+Enter (Option+Enter without CSI u mode) inserts a newline."""
        event.current_buffer.insert_text("\n")

    # Trigger and maintain completion for slash commands
    def _handle_char_for_completion(event, char: str):
        """Insert character and trigger completion if in a slash command."""
        buffer = event.current_buffer
        buffer.insert_text(char)

        # Check if we're typing a slash or bang command
        text = buffer.text[: buffer.cursor_position]
        if (
            text.startswith("/")
            or text.startswith("!")
            or (("\n/" in text) and text.rsplit("\n", 1)[-1].startswith("/"))
        ):
            buffer.start_completion(select_first=False)

    if not vi_mode:
        # Bind alphanumeric + path-relevant punctuation to maintain completion.
        # Skipped in vi mode: letters are vi normal-mode movement/action keys.
        _completion_chars = (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            "._-~"  # common in file paths: dotfiles, underscores, hyphens, home ~
        )
        for char in _completion_chars:

            @bindings.add(char)
            def _(event, c=char):
                _handle_char_for_completion(event, c)

        @bindings.add(" ")
        def _(event):
            _handle_char_for_completion(event, " ")

        @bindings.add("c-h")  # Backspace
        @bindings.add("backspace")
        def _(event):
            """Handle backspace and re-trigger completion if still in / or ! command."""
            buffer = event.current_buffer
            buffer.delete_before_cursor(1)

            text = buffer.text[: buffer.cursor_position]
            if text.startswith("/") or text.startswith("!"):
                buffer.start_completion(select_first=False)

    # Always bind "/" — in vi mode only trigger completion when the buffer
    # already starts with "/" (i.e. we're already in a slash command), so that
    # vi normal-mode "/" search still fires on an empty buffer.
    @bindings.add("/")
    def _(event):
        buffer = event.current_buffer
        text_before = buffer.text[: buffer.cursor_position]
        if vi_mode and not text_before.startswith("/"):
            # Let vi handle "/" search on an empty/non-slash buffer
            buffer.insert_text("/")
        else:
            _handle_char_for_completion(event, "/")

    # "!" on an empty buffer starts a bang command; trigger completion immediately.
    @bindings.add("!")
    def _(event):
        buffer = event.current_buffer
        buffer.insert_text("!")
        text = buffer.text[: buffer.cursor_position]
        if text == "!" or text.startswith("!"):
            buffer.start_completion(select_first=False)

    return bindings


def create_typeahead_key_bindings(state: QueueState, vi_mode: bool = False) -> KeyBindings:
    """Key bindings for the type-ahead prompt used during agent work.

    Differences from the normal input bindings:

    - Enter does NOT call ``validate_and_handle`` (which would exit the prompt).
      Instead it submits the current buffer text to ``state`` and clears the
      buffer. The enclosing prompt_async keeps running; it only exits when
      ``app.exit()`` is called externally (when the agent finishes) or the
      user presses Ctrl+C / Ctrl+D.
    - Up on an empty buffer pops the most recent queued item into the buffer
      for editing (when the queue is non-empty). Otherwise falls through to
      normal history navigation.
    """
    bindings = KeyBindings()

    @bindings.add("enter", eager=True)
    def _(event):
        """Submit the current buffer to the queue, then stay in the prompt."""
        buffer = event.current_buffer
        text = buffer.text
        buffer.reset()
        state.submit(text)
        event.app.invalidate()

    @bindings.add("c-j")
    def _(event):
        """Shift+Enter on iTerm2 sends Ctrl+J — insert a newline."""
        event.current_buffer.insert_text("\n")

    @bindings.add("escape", "enter")
    def _(event):
        """Alt/Option+Enter inserts a newline."""
        event.current_buffer.insert_text("\n")

    @bindings.add("escape")
    def _(event):
        """Esc: cancel the agent, then deliver any queued messages.

        Sets ``state.cancel_requested`` and exits the prompt. The session
        reads the flag after the prompt returns, cancels the agent task,
        and delivers whatever is queued as the next ``respond()`` — or
        returns to the between-turn prompt if the queue is empty.

        Not ``eager=True``: bare Escape is also the prefix for ESC-sequence
        input (arrow keys, Alt-combinations, CSI sequences). Firing
        eagerly would eat the prefix and misinterpret every arrow key as
        Esc-cancel. Without eager, prompt_toolkit waits for the
        disambiguation timeout (~0.5s) before firing the bare-Esc
        binding — small delay, correct behaviour.
        """
        state.cancel_requested = True
        event.app.exit(result="")

    def _can_pop_queue() -> bool:
        try:
            buffer_empty = not get_app().current_buffer.text
        except Exception:
            return False
        return buffer_empty and not state.is_empty

    @bindings.add("up", filter=Condition(_can_pop_queue))
    def _(event):
        """Pop last queued item back into the buffer for editing."""
        popped = state.pop_last_for_edit()
        if popped is not None:
            buffer = event.current_buffer
            buffer.text = popped
            buffer.cursor_position = len(popped)
            event.app.invalidate()

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

    def __init__(self, registry: "CommandRegistry", vi_mode: bool = False):
        """Initialize the input handler.

        Args:
            registry: CommandRegistry instance to get completion options from
            vi_mode: Enable vi keybindings (normal/insert mode). When True,
                     letter-key completion bindings are skipped to avoid
                     conflicts with vi movement commands.
        """
        from prompt_toolkit.shortcuts import CompleteStyle

        self.vi_mode = vi_mode
        self.history = InMemoryHistory()
        self.completer = SlashCommandCompleter(registry)
        self.key_bindings = create_key_bindings(vi_mode=vi_mode)
        self.style = create_prompt_style()

        self.session = PromptSession(
            history=self.history,
            completer=self.completer,
            key_bindings=self.key_bindings,
            style=self.style,
            multiline=True,  # Allow Shift+Enter newlines; Enter binding submits
            complete_while_typing=False,  # We handle it manually via key bindings
            complete_style=CompleteStyle.COLUMN,  # Single column with descriptions
            enable_history_search=True,  # Ctrl+R for reverse search
            vi_mode=vi_mode,
        )

    def refresh_style(self) -> None:
        """Rebuild prompt_toolkit style from current theme colors."""
        self.style = create_prompt_style()
        self.session.style = self.style

    async def get_input(self, prompt: str = "You: ", default: str = "", bottom_toolbar=None) -> str:
        """Get input from user with history and completion.

        Args:
            prompt: The prompt to display
            default: Pre-fill the input buffer with this text
            bottom_toolbar: Optional callable returning toolbar text (for spinner)

        Returns:
            User input string (may be multi-line)

        Raises:
            EOFError: If Ctrl+D is pressed
            KeyboardInterrupt: If Ctrl+C is pressed
        """
        kwargs: dict = {
            "prompt_continuation": "",
            "default": default,
        }
        if bottom_toolbar is not None:
            kwargs["bottom_toolbar"] = bottom_toolbar
        result = await self.session.prompt_async(
            [("class:prompt", prompt)],
            **kwargs,
        )
        return result.strip()

    def invalidate(self) -> None:
        """Force a redraw of the prompt (used by spinner to update toolbar)."""
        if self.session.app:
            self.session.app.invalidate()
