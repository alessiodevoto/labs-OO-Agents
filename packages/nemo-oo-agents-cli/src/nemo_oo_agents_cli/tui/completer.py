# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared completion engine for NeMo OO Agents frontends.

``Completer`` is frontend-agnostic: it takes a text buffer and returns a list
of ``CompletionItem`` objects.  Both the terminal (prompt_toolkit adapter) and
the web frontend (WebSocket round-trip) use this same engine, so completion
behavior is identical everywhere.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import CommandRegistry


# Built-in ! commands
_BANG_BUILTINS: dict[str, str] = {}


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
    """

    def __init__(
        self,
        registry: "CommandRegistry",
    ) -> None:
        self._registry = registry

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

        # Job name completion after "/jobs "
        if lower.startswith("/jobs "):
            return self._job_name_completions(text)

        # Theme name completion
        if lower.startswith("/theme "):
            return self._theme_completions(text)

        # Model completion
        if lower.startswith("/switch ") or lower.startswith("/model "):
            return self._model_completions(text)

        # Skill ID completion for /skills activate and /skills deactivate
        for prefix in ("/skills activate ", "/skills deactivate "):
            if lower.startswith(prefix.lower()):
                return self._skill_id_completions(text, prefix)

        # Session ID completion
        for prefix in ("/session resume ", "/session delete "):
            if lower.startswith(prefix.lower()):
                return self._session_id_completions(text, prefix)

        # Todo ID completion
        if lower.startswith("/todo "):
            return self._todo_id_completions(text)

        # Event tag completion — always return a non-empty list to
        # prevent prompt_toolkit's CompletionsMenu from crashing on
        # max() of an empty iterable.
        if lower.startswith("/events "):
            return self._event_tag_completions(text) or [
                CompletionItem(text=text, display=text, description="(no matching tags)")
            ]

        # Top-level slash commands + subcommands.
        # get_active_help() keys may include argument hints ("/wtf-status [label]").
        # We strip the hint for text (insertion) but keep it for display.
        commands = self._registry.get_active_help()
        seen: set[str] = set()
        items: list[CompletionItem] = []
        for display_cmd, desc in commands.items():
            clean = re.split(r"\s+(?=[<\[])", display_cmd, maxsplit=1)[0].strip()
            if clean in seen:
                continue
            if clean.lower().startswith(text.lower()):
                seen.add(clean)
                items.append(CompletionItem(text=clean, display=display_cmd, description=desc))
        return items

    # ------------------------------------------------------------------
    # Theme completion
    # ------------------------------------------------------------------

    def _theme_completions(self, text: str) -> list[CompletionItem]:
        from .theme import THEMES

        prefix = "/theme "
        partial = text[len(prefix) :]
        items = []
        for name in THEMES:
            if name.startswith(partial.lower()):
                items.append(
                    CompletionItem(
                        text=prefix + name,
                        display=prefix + name,
                        description=f"Switch to {name} theme",
                    )
                )
        return items

    # ------------------------------------------------------------------
    # Model completion
    # ------------------------------------------------------------------

    def _model_completions(self, text: str) -> list[CompletionItem]:
        try:
            from nemo_oo_agents.unifiedllm import MODELS
        except Exception:
            return []

        # Detect which command triggered this
        lower = text.lower()
        prefix = "/model " if lower.startswith("/model ") else "/switch "
        partial = text[len(prefix) :]
        items = []
        for name in sorted(MODELS.keys()):
            if name.lower().startswith(partial.lower()):
                items.append(
                    CompletionItem(
                        text=prefix + name,
                        display=prefix + name,
                        description=f"Switch to {name}",
                    )
                )
        return items

    # ------------------------------------------------------------------
    # Skill ID completion
    # ------------------------------------------------------------------

    def _skill_id_completions(self, text: str, prefix: str) -> list[CompletionItem]:
        try:
            from nemo_oo_agents import SkillManager
        except ImportError:
            return []

        skills_dirs = getattr(self._registry, "skills_dirs", None)
        if not skills_dirs:
            return []

        partial = text[len(prefix) :]
        try:
            skills = SkillManager.discover(skills_dirs)
        except Exception:
            return []

        items = []
        for sid, skill in sorted(skills.items()):
            if partial and not sid.startswith(partial):
                continue
            desc = getattr(skill, "description", "")
            items.append(
                CompletionItem(
                    text=prefix + sid,
                    display=prefix + sid,
                    description=desc,
                )
            )
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
    # Todo ID completion
    # ------------------------------------------------------------------

    def _todo_id_completions(self, text: str) -> list[CompletionItem]:
        prefix = "/todo "
        partial = text[len(prefix) :]
        agent = getattr(self._registry, "agent", None)
        todo_mgr = getattr(agent, "todo", None) if agent else None
        if todo_mgr is None:
            return []

        items: list[CompletionItem] = []
        icons = {"open": "○", "done": "✓", "blocked": "●"}
        for t in todo_mgr.list_todos():
            if partial and not t.id.startswith(partial):
                continue
            icon = icons.get(t.status, "?")
            items.append(
                CompletionItem(
                    text=prefix + t.id,
                    display=prefix + t.id,
                    description=f"{icon} {t.title}",
                )
            )
        return items

    def _job_name_completions(self, text: str) -> list[CompletionItem]:
        prefix = "/jobs "
        partial = text[len(prefix) :].lower()
        agent = getattr(self._registry, "agent", None)
        qm = getattr(agent, "queue_manager", None) if agent else None
        if qm is None:
            return []

        items: list[CompletionItem] = []
        icons = {"running": "⏳", "done": "✅", "failed": "❌", "cancelled": "⏹"}
        for name, state in sorted(qm.jobs().items()):
            if partial and not name.lower().startswith(partial):
                continue
            handle = qm.job(name)
            buf = f" ({len(handle.values)} delivered)" if handle and handle.values else ""
            items.append(
                CompletionItem(
                    text=prefix + name,
                    display=prefix + name,
                    description=f"{icons.get(state, '?')} {state}{buf}",
                )
            )
        return items

    # ------------------------------------------------------------------
    # Event tag completion
    # ------------------------------------------------------------------

    def _event_tag_completions(self, text: str) -> list[CompletionItem]:
        prefix = "/events "
        partial = text[len(prefix) :]
        agent = getattr(self._registry, "agent", None)
        em = getattr(agent, "event_manager", None) if agent else None
        if em is None:
            return []

        icons = {
            "ToolCallEvent": "\U0001f527",
            "PythonOutput": "\U0001f4e4",
            "Task": "\U0001f4cb",
            "TUIUserInput": "\U0001f4ac",
            "TUISessionStart": "\U0001f680",
            "Summary": "\U0001f4dd",
            "Error": "\u274c",
        }

        items: list[CompletionItem] = []
        for tag in em.keys():
            if partial and not tag.startswith(partial):
                continue
            if tag == partial:
                continue  # skip exact match — already typed
            event = em.get(tag)
            if event is None:
                continue
            etype = getattr(event, "event_type", type(event).__name__)
            icon = icons.get(etype, "\U0001f4cc")

            # One-line summary
            summary = self._event_summary(event, etype)
            items.append(
                CompletionItem(
                    text=prefix + tag,
                    display=prefix + tag,
                    description=f"{icon} {summary}",
                )
            )
        return items

    @staticmethod
    def _event_summary(event, etype: str) -> str:
        """One-line summary for event tag completion."""
        if etype == "ToolCallEvent":
            name = getattr(event, "name", "?")
            args = getattr(event, "arguments", {})
            if name == "execute_python" and isinstance(args, dict) and "code" in args:
                for line in args["code"].split("\n"):
                    s = line.strip()
                    if s and not s.startswith("#"):
                        return f"{name} \u2014 {s[:50]}"
            return name
        elif etype == "PythonOutput":
            status = str(getattr(event, "execution_status", "?"))
            label = status.rsplit(".", 1)[-1]
            icon = "\u2705" if label == "complete" else "\u274c"
            stdout = getattr(event, "stdout", "") or ""
            error = getattr(event, "error", "") or ""
            if label != "complete" and error:
                return f"{icon} {error.strip().splitlines()[-1][:50]}"
            elif stdout:
                return f"{icon} {stdout.strip().splitlines()[0][:50]}"
            return f"{icon} {label}"
        elif etype == "TUIUserInput":
            text = getattr(event, "text", "") or ""
            return text[:50] + ("..." if len(text) > 50 else "")
        elif etype == "TUISessionStart":
            return f"model={getattr(event, 'model', '?')}"
        elif etype == "Task":
            prompt = getattr(event, "prompt", "") or ""
            first = prompt.strip().splitlines()[0] if prompt.strip() else ""
            return first[:50]
        return str(event)[:50]

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
