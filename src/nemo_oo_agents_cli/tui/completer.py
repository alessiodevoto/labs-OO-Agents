"""Shared completion engine for NeMo OO Agents frontends.

``Completer`` is frontend-agnostic: it takes a text buffer and returns a list
of ``CompletionItem`` objects.  Both the terminal (prompt_toolkit adapter) and
the web frontend (WebSocket round-trip) use this same engine, so completion
behavior is identical everywhere.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import CommandRegistry
    from .session_manager import SessionManager


# Built-in ! commands
_BANG_BUILTINS: dict[str, str] = {
    "!python": "Open embedded IPython shell with agent in scope",
    "!ipython": "Open embedded IPython shell with agent in scope",
}


@dataclass(frozen=True)
class CompletionItem:
    """A single completion candidate."""

    text: str  # what gets inserted (the full replacement text)
    display: str  # what shows in the menu
    description: str = ""  # help text / description


class Completer:
    """Frontend-agnostic completion engine.

    Args:
        registry: CommandRegistry to pull slash-command completions from.
        session_manager_fn: Callable returning the current SessionManager
            (or None).  Called lazily so it tracks session swaps.
    """

    def __init__(
        self,
        registry: "CommandRegistry",
        session_manager_fn: "Callable[[], SessionManager | None] | None" = None,
    ) -> None:
        self._registry = registry
        self._session_manager_fn = session_manager_fn

    def complete(self, text: str) -> list[CompletionItem]:
        """Return completion candidates for *text*."""
        if text.startswith("/"):
            return self._slash_completions(text)
        if text.startswith("!"):
            return self._bang_completions(text)
        return []

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def _slash_completions(self, text: str) -> list[CompletionItem]:
        # Path completion after "/edit "
        lower = text.lower()
        if lower.startswith("/edit "):
            return self._path_completions(text[6:], prefix="/edit ")

        # Session ID completion
        for prefix in ("/session resume ", "/session delete "):
            if lower.startswith(prefix.lower()):
                return self._session_id_completions(text, prefix)

        # Top-level slash commands + subcommands
        commands = self._registry.get_completions()
        items: list[CompletionItem] = []
        for cmd, desc in commands.items():
            if cmd.lower().startswith(text.lower()):
                items.append(CompletionItem(text=cmd, display=cmd, description=desc))
        return items

    # ------------------------------------------------------------------
    # Session ID completion
    # ------------------------------------------------------------------

    def _session_id_completions(self, text: str, prefix: str) -> list[CompletionItem]:
        from .session_manager import SessionManager

        partial = text[len(prefix) :]
        try:
            sessions = SessionManager.list_sessions(limit=50)
        except Exception:
            return []

        items: list[CompletionItem] = []
        for meta in sessions:
            sid = meta.id
            short = sid[:8]
            if partial and not short.startswith(partial) and not sid.startswith(partial):
                continue
            items.append(
                CompletionItem(
                    text=prefix + short,
                    display=prefix + short,
                    description=meta.name or sid,
                )
            )
        return items

    # ------------------------------------------------------------------
    # Bang commands
    # ------------------------------------------------------------------

    def _bang_completions(self, text: str) -> list[CompletionItem]:
        items: list[CompletionItem] = []

        # Built-in bang commands
        for cmd, desc in _BANG_BUILTINS.items():
            if cmd.lower().startswith(text.lower()):
                items.append(CompletionItem(text=cmd, display=cmd, description=desc))

        # Path completions after "!<cmd> "
        rest = text[1:]  # strip leading !
        if " " in rest or (
            rest and not any(rest.lstrip().startswith(b[1:]) for b in _BANG_BUILTINS)
        ):
            items.extend(self._path_completions(rest, prefix="!"))

        return items

    # ------------------------------------------------------------------
    # File path completion
    # ------------------------------------------------------------------

    def _path_completions(self, partial: str, prefix: str = "") -> list[CompletionItem]:
        """Complete filesystem paths from *partial*.

        Args:
            partial: The path fragment the user has typed so far.
            prefix: Command prefix to prepend to each item's ``text`` so that
                    the result is a full input replacement (e.g. ``"/edit "``).
        """
        if not partial:
            partial = "."

        # Expand ~ to home dir
        expanded = os.path.expanduser(partial)
        p = Path(expanded)

        # Determine directory to list and filter prefix
        if p.is_dir():
            parent = p
            name_filter = ""
        else:
            parent = p.parent
            name_filter = p.name

        if not parent.exists():
            return []

        items: list[CompletionItem] = []
        try:
            for entry in sorted(parent.iterdir()):
                name = entry.name
                if name_filter and not name.lower().startswith(name_filter.lower()):
                    continue
                # Skip hidden files unless the user typed a dot
                if name.startswith(".") and not name_filter.startswith("."):
                    continue

                display = name + ("/" if entry.is_dir() else "")
                # Build the path portion
                if p.is_dir():
                    path_text = str(Path(partial) / name)
                else:
                    path_text = str(Path(partial).parent / name)

                if entry.is_dir():
                    path_text += "/"

                # Full input replacement = command prefix + path
                items.append(
                    CompletionItem(
                        text=prefix + path_text,
                        display=display,
                        description="",
                    )
                )
        except PermissionError:
            pass

        return items
