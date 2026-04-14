# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Structured output types for NeMo OO Agents frontends.

Commands and agent events produce ``Output`` instances; each ``Frontend``
renders them in its own way — Rich panels in the terminal, JSON over a
WebSocket for the browser.

Every concrete type is a plain dataclass with a ``to_json()`` method that
returns a dict suitable for JSON serialisation.  This is the **single source
of truth** for web serialisation — ``WebFrontend`` calls ``output.to_json()``
and sends the result over the WebSocket.  Adding a new Output type
automatically works in both frontends as long as ``to_json()`` is defined.
"""

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Concrete output types
# ---------------------------------------------------------------------------


@dataclass
class TextOutput:
    """A plain text message with a severity level."""

    content: str
    level: Literal["info", "error", "warning", "success", "status"] = "info"

    def to_json(self) -> dict:
        return {"type": "text", "content": self.content, "level": self.level}


@dataclass
class TableOutput:
    """Tabular data."""

    columns: list[str]
    rows: list[list[str]]
    title: str = ""
    footer: str = ""

    def to_json(self) -> dict:
        return {
            "type": "table",
            "title": self.title,
            "columns": self.columns,
            "rows": self.rows,
            "footer": self.footer,
        }


@dataclass
class HelpOutput:
    """Slash-command help listing."""

    commands: dict[str, str]  # {"/cmd subcmd": "description"}

    def to_json(self) -> dict:
        return {"type": "help", "commands": self.commands}


@dataclass
class AgentMessage:
    """A markdown-formatted message produced by the agent via ``message()``."""

    content: str
    # False for 2nd+ message() calls in the same turn — suppresses the OO ── rule
    show_rule: bool = True

    def to_json(self) -> dict:
        return {"type": "agent_message", "content": self.content, "show_rule": self.show_rule}


@dataclass
class ActivityLine:
    """A single-line live activity preview shown while the agent is working.

    ``kind`` is one of:
    - ``"reasoning"`` — dim italic chain-of-thought snippet
    - ``"code"``      — first line(s) of code about to be executed
    """

    content: str
    kind: Literal["reasoning", "code"] = "reasoning"

    def to_json(self) -> dict:
        return {"type": "activity", "content": self.content, "kind": self.kind}


@dataclass
class CodeExecution:
    """One Python execution turn: code + outputs."""

    tool_call_id: str
    code: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    value: str | None = None

    def to_json(self) -> dict:
        v = self.value
        if v is not None and not isinstance(v, str):
            v = repr(v)
        return {
            "type": "code_execution",
            "tool_call_id": self.tool_call_id,
            "code": self.code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "value": v,
        }


@dataclass
class StartupInfo:
    """Structured startup banner data."""

    model: str  # full model id
    short_model: str  # display name
    working_dir: str
    vi_mode: bool
    history_policy: str | None = None
    history_limit: int | None = None
    sandbox_available: bool | None = None
    tracing_enabled: bool = False
    trace_dir: str | None = None
    custom_agent: str | None = None

    def to_json(self) -> dict:
        return {
            "type": "startup",
            "model": self.model,
            "short_model": self.short_model,
            "working_dir": self.working_dir,
            "vi_mode": self.vi_mode,
            "history_policy": self.history_policy,
            "history_limit": self.history_limit,
            "sandbox_available": self.sandbox_available,
            "tracing_enabled": self.tracing_enabled,
            "trace_dir": self.trace_dir,
            "custom_agent": self.custom_agent,
        }


@dataclass
class ClearScreen:
    """Clear the visible output area."""

    def to_json(self) -> dict:
        return {"type": "clear"}


@dataclass
class SessionEnd:
    """Signal to the frontend that the session has ended cleanly."""

    def to_json(self) -> dict:
        return {"type": "bye"}


@dataclass
class Thinking:
    """Show or hide the loading/thinking indicator."""

    active: bool
    message: str = "thinking..."

    def to_json(self) -> dict:
        return {
            "type": "thinking_start" if self.active else "thinking_stop",
            "message": self.message,
        }


@dataclass
class BashOutput:
    """Result from a ``!bash`` command."""

    stdout: str
    stderr: str
    return_code: int

    def to_json(self) -> dict:
        return {
            "type": "bash_output",
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
        }


@dataclass
class HistoryTurn:
    """One turn in a replayed session history."""

    role: Literal["user", "agent"]
    content: str

    def to_json(self) -> dict:
        return {"type": "history_turn", "role": self.role, "content": self.content}


@dataclass
class HistoryReplay:
    """A block of past conversation turns rendered above the live prompt.

    Used when resuming or continuing a session so the user can see what was
    said before.  Frontends should render this in a visually dimmed / muted
    style to distinguish it from the live conversation.

    When rich content is interleaved with history (session resume inside
    ``nemo oo term``), one session's history may be split into several
    ``HistoryReplay`` chunks with ``_RichReplayPayload`` objects between them.
    ``show_header`` / ``show_footer`` control which chunk renders the enclosing
    rule bars so they appear exactly once around the whole block.
    """

    turns: list[HistoryTurn]
    session_id: str  # short (8-char) id for the header label
    show_header: bool = True
    show_footer: bool = True

    def to_json(self) -> dict:
        return {
            "type": "history_replay",
            "session_id": self.session_id,
            "turns": [t.to_json() for t in self.turns],
        }


@dataclass
class DiffOutput:
    """A unified diff between two versions of a file, shown after /edit saves."""

    diff: str
    filename: str = ""

    def to_json(self) -> dict:
        return {"type": "diff", "diff": self.diff, "filename": self.filename}


@dataclass
class RichOutput:
    """Generic rich visual output for frontends that support it.

    The ``kind`` field identifies the rendering type; ``data`` is the
    kind-specific payload.  Terminal frontends fall back to ``fallback_text``;
    web frontends render the real thing.

    Built-in kinds recognised by the web frontend:

    ``"plotly"``
        ``data["figure_json"]`` — result of ``plotly.Figure.to_json()``.
        Rendered via Plotly.js.

    ``"html"``
        ``data["html"]`` — arbitrary safe HTML fragment.

    ``"image"``
        ``data["src"]`` — a ``data:`` URI or URL.
        ``data["alt"]`` — optional alt-text.

    ``"vega"``
        ``data["spec"]`` — a Vega-Lite or Vega spec dict.
        Rendered via the Vega CDN.

    ``"dataframe"``
        ``data["columns"]``, ``data["rows"]`` — rendered as a sortable table.

    Any other ``kind`` value is forwarded as-is to the frontend; unknown
    kinds are displayed as a JSON code block.
    """

    kind: str
    data: dict
    title: str = ""
    fallback_text: str = ""  # shown in terminal / non-graphical frontends

    def to_json(self) -> dict:
        return {
            "type": "rich",
            "kind": self.kind,
            "data": self.data,
            "title": self.title,
            "fallback_text": self.fallback_text,
        }


# ---------------------------------------------------------------------------
# _RichReplayPayload — internal sentinel for interleaved rich-content replay
# ---------------------------------------------------------------------------


@dataclass
class _RichReplayPayload:
    """Carry a raw WebPublisher payload through an output list.

    Not part of the public ``Output`` union — intercepted by ``CommandHandler``
    and the ``--continue`` startup path before reaching any frontend renderer.
    Each instance is POSTed to ``NEMO_RICH_URL`` in sequence so that plots
    appear at their correct inline positions between history turns.
    """

    payload: dict


# ---------------------------------------------------------------------------
# Union alias used in type annotations
# ---------------------------------------------------------------------------

Output = (
    TextOutput
    | TableOutput
    | HelpOutput
    | AgentMessage
    | ActivityLine
    | CodeExecution
    | StartupInfo
    | ClearScreen
    | SessionEnd
    | Thinking
    | BashOutput
    | DiffOutput
    | RichOutput
    | HistoryReplay
)
