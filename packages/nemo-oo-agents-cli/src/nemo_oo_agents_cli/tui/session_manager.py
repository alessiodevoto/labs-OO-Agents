# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Session management — UUID-keyed persistent conversation history.

Each session gets a ``SQLiteStorageManager`` at
``<project-root>/.nemo_oo_agents/sessions/<uuid>.db``.  TUI metadata (session start info,
user input, renames) is stored as ``Metadata`` events via the event
manager.  Agent turns are reconstructed from ``Message`` events already
recorded by the agent framework.

The ``SessionManager`` wraps the per-session ``SQLiteStorageManager`` and
exposes the same public API as before (``record_user``,
``list_sessions``, ``load_turns``, etc.).
"""

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from nemo_oo_agents.storage import SQLiteStorageManager

from nemo_oo_agents.paths import get_project_dir

SESSIONS_DIR = get_project_dir("sessions")


def _make_trace_session_name(session_id: str) -> str:
    """Build a human-readable trace session name correlated to a SQLite session UUID.

    Format: ``tui-YYYYMMDD-HHMMSS-<first8_of_uuid>``
    The 8-char suffix matches the corresponding ``.db`` filename so trace files
    and storage files are trivially correlated.
    """
    from datetime import UTC, datetime

    return f"tui-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{session_id[:8]}"


@dataclass
class SessionMeta:
    """Lightweight session metadata."""

    id: str
    model: str
    agent: str
    started_at: float
    last_active: float
    turn_count: int = 0
    working_dir: str = ""
    name: str | None = None
    user_named: bool = False


@dataclass
class Turn:
    role: Literal["user", "agent"]
    content: str
    ts: float = field(default_factory=time.time)


def _open_session_db(session_id: str) -> sqlite3.Connection:
    """Open the raw SQLite connection for a session DB (read-only ops)."""
    path = SESSIONS_DIR / f"{session_id}.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


class SessionManager:
    """Manages a single live session using the per-session SQLiteStorageManager."""

    def __init__(
        self,
        storage: "SQLiteStorageManager",
        session_id: str | None = None,
        model: str = "",
        agent_cls: str = "TUIAgent",
        working_dir: str = "",
        *,
        resumed: bool = False,
    ) -> None:
        from nemo_oo_agents.runtime.event_manager import EventManager

        from .tui_events import TUI_EVENT_TYPES, TUISessionStart

        self.session_id = session_id or str(uuid.uuid4())
        self.model = model
        self.agent_cls = agent_cls
        self.working_dir = working_dir
        self._storage = storage
        self._name: str | None = None
        self._user_named: bool = False

        # Thin EventManager bound to storage so SessionManager can
        # write TUI metadata without depending on the agent (which
        # doesn't exist yet at construction). Tag allocation lives on
        # the backend, so this manager and the agent's coexist safely.
        self._event_manager = EventManager(backend=storage.event_backend)

        # TUI event types are auto-registered in the global _EVENT_REGISTRY
        # via __pydantic_init_subclass__, so these per-instance register calls
        # are no longer strictly necessary.  Kept for backward compatibility
        # with any code that relies on the per-backend registry.
        for cls in TUI_EVENT_TYPES:
            self._event_manager.register_event_type(cls)

        if resumed:
            # Restore name/user_named from existing events
            meta = self._read_meta(Path(storage._db_path))
            if meta is not None:
                self._name = meta.name
                self._user_named = meta.user_named
        else:
            # Write session-start metadata event
            self._event_manager.add(
                TUISessionStart(
                    model=model,
                    agent_cls=agent_cls,
                    working_dir=working_dir,
                )
            )

    @property
    def agent_db_path(self) -> Path:
        """Path for the per-session agent state DB."""
        return SESSIONS_DIR / f"{self.session_id}.db"

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def user_named(self) -> bool:
        return self._user_named

    def rename(self, name: str, user_named: bool = False) -> None:
        """Set the session name and persist it as a metadata event."""
        from .tui_events import TUISessionRename

        self._name = name
        if user_named:
            self._user_named = True
        self._event_manager.add(
            TUISessionRename(
                name=name,
                user_named=user_named,
            )
        )

    def update_agent_cls(self, agent_cls: str) -> None:
        """Update the stored agent class name (called after custom agent loads)."""
        self.agent_cls = agent_cls

    def record_user(self, text: str) -> None:
        """Store the user's raw input as a TUIUserInput metadata event."""
        from .tui_events import TUIUserInput

        self._event_manager.add(TUIUserInput(text=text))

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self._storage.close()
        except Exception:
            pass

    @property
    def turns(self) -> list[Turn]:
        """In-memory turns reconstructed from stored events."""
        return self.load_turns(self.session_id)

    def as_markdown(self) -> str:
        lines: list[str] = [f"# Session {self.session_id[:8]}\n"]
        for t in self.turns:
            prefix = "**You:**" if t.role == "user" else "**NeMo OO Agents:**"
            lines.append(f"{prefix}\n\n{t.content}\n")
        return "\n---\n\n".join(lines)

    # ------------------------------------------------------------------
    # Class-level operations on stored sessions
    # ------------------------------------------------------------------

    @classmethod
    def list_sessions(cls, limit: int = 20) -> list[SessionMeta]:
        """Return recent sessions sorted newest-first by scanning session DBs."""
        if not SESSIONS_DIR.exists():
            return []

        metas: list[SessionMeta] = []
        db_files = sorted(
            SESSIONS_DIR.glob("*.db"),
            key=lambda p: -p.stat().st_mtime,
        )[: limit * 2]  # read more than needed in case some are corrupt

        for path in db_files:
            meta = cls._read_meta(path)
            if meta is not None:
                metas.append(meta)
            if len(metas) >= limit:
                break

        return metas

    @classmethod
    def _read_meta(cls, path: Path) -> SessionMeta | None:
        """Read session metadata from a per-session DB."""
        from .tui_events import TUISessionRename, TUISessionStart

        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT event_type, data, insertion_order FROM events ORDER BY insertion_order"
            ).fetchall()
            conn.close()
        except Exception:
            return None

        session_id = path.stem
        start_ts: float = path.stat().st_mtime
        last_ts: float = start_ts
        start_event: TUISessionStart | None = None
        name: str | None = None
        user_named: bool = False
        turn_count: int = 0

        for row in rows:
            try:
                raw = json.loads(row["data"])
                et = row["event_type"]
                ts = raw.get("timestamp")
                if ts:
                    try:
                        from datetime import datetime

                        last_ts = datetime.fromisoformat(ts).timestamp()
                    except Exception:
                        pass

                if et == "TUISessionStart" and start_event is None:
                    start_event = TUISessionStart.model_validate(raw)
                    start_ts = last_ts
                elif et == "TUISessionRename":
                    ev = TUISessionRename.model_validate(raw)
                    name = ev.name or None
                    user_named = ev.user_named
                elif et in ("TUIUserInput", "TUIAgentMessage"):
                    turn_count += 1
            except Exception:
                continue

        if start_event is None:
            return None

        return SessionMeta(
            id=session_id,
            model=start_event.model,
            agent=start_event.agent_cls,
            started_at=start_ts,
            last_active=last_ts,
            turn_count=turn_count,
            working_dir=start_event.working_dir,
            name=name,
            user_named=user_named,
        )

    @classmethod
    def load_turns(cls, session_id: str) -> list[Turn]:
        """Reconstruct conversation turns from stored events."""
        from .tui_events import TUIUserInput

        path = SESSIONS_DIR / f"{session_id}.db"
        if not path.exists():
            return []

        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT event_type, data FROM events "
                "WHERE event_type IN ('TUIUserInput', 'TUIAgentMessage') "
                "ORDER BY insertion_order"
            ).fetchall()
            conn.close()
        except Exception:
            return []

        turns: list[Turn] = []
        for row in rows:
            try:
                raw = json.loads(row["data"])
                ts_str = raw.get("timestamp")
                ts = time.time()
                if ts_str:
                    try:
                        from datetime import datetime

                        ts = datetime.fromisoformat(ts_str).timestamp()
                    except Exception:
                        pass

                if row["event_type"] == "TUIUserInput":
                    ev = TUIUserInput.model_validate(raw)
                    turns.append(Turn(role="user", content=ev.text, ts=ts))
                elif row["event_type"] == "TUIAgentMessage":
                    turns.append(Turn(role="agent", content=raw.get("content", ""), ts=ts))
            except Exception:
                continue

        return turns

    @classmethod
    def find_by_prefix(cls, prefix: str) -> list[str]:
        """Return session IDs (DB stems) whose ID starts with prefix."""
        if not SESSIONS_DIR.exists():
            return []
        matches = [p.stem for p in SESSIONS_DIR.glob(f"{prefix}*.db")]
        return sorted(matches, key=lambda sid: -(SESSIONS_DIR / f"{sid}.db").stat().st_mtime)

    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        path = SESSIONS_DIR / f"{session_id}.db"
        if not path.exists():
            return False
        path.unlink()
        # Clean up WAL-mode auxiliary files
        for suffix in (".db-wal", ".db-shm"):
            aux = SESSIONS_DIR / f"{session_id}{suffix}"
            if aux.exists():
                aux.unlink()
        return True


# Default number of turns to show on session resume.  Keeps the terminal
# responsive even for very long sessions.
RESUME_MAX_TURNS: int = 20


def build_resume_outputs(
    session_db_path: Path,
    session_id: str,
    *,
    in_nemo_term: bool = False,
    max_turns: int | None = None,
) -> list:
    """Build an interleaved output list for session resume / startup replay.

    Reads all events from *session_db_path* in insertion order and returns a
    list mixing ``HistoryReplay`` chunks (conversation turns) with
    ``_RichReplayPayload`` sentinels (inline plots/content) so that each plot
    appears at its original position between the surrounding turns.

    When *in_nemo_term* is False (plain TUI) rich events are ignored and the
    list contains a single ``HistoryReplay`` with all turns — matching the
    original behaviour.

    Args:
        max_turns: Maximum number of conversation turns to replay.  Older
            turns are omitted with a summary line.  Defaults to
            ``RESUME_MAX_TURNS``.  Pass ``0`` to disable truncation.

    Callers are responsible for rendering each item:
    - ``HistoryReplay`` → ``await frontend.render(item)``
    - ``_RichReplayPayload`` → ``httpx.post(NEMO_RICH_URL, json=item.payload)``
    """
    import sqlite3 as _sqlite3

    from .output import HistoryReplay, HistoryTurn, _RichReplayPayload

    if max_turns is None:
        max_turns = RESUME_MAX_TURNS

    try:
        conn = _sqlite3.connect(str(session_db_path))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT event_type, data FROM events ORDER BY insertion_order"
        ).fetchall()
        conn.close()
    except Exception:
        rows = []

    # First pass: collect ordered items
    items: list[tuple[str, object]] = []  # ("turns", list) | ("rich", dict)
    pending: list[HistoryTurn] = []

    for row in rows:
        et = row["event_type"]
        try:
            raw = json.loads(row["data"])
        except Exception:
            continue
        if et == "TUIUserInput" and raw.get("text"):
            pending.append(HistoryTurn(role="user", content=raw["text"]))
        elif et == "TUIAgentMessage" and raw.get("content"):
            pending.append(HistoryTurn(role="agent", content=raw["content"]))
        elif et == "RichOutput" and in_nemo_term and raw.get("payload"):
            if pending:
                items.append(("turns", pending[:]))
                pending = []
            items.append(("rich", raw["payload"]))

    if pending:
        items.append(("turns", pending))

    if not items:
        return []

    # ── Truncation ─────────────────────────────────────────────────────
    # Count total conversation turns across all chunks and trim to the
    # most recent max_turns.  Rich items interspersed among kept turns
    # are preserved; those among dropped turns are discarded.
    total_turns = sum(len(d) for k, d in items if k == "turns")
    omitted = 0
    if max_turns and total_turns > max_turns:
        omitted = total_turns - max_turns
        # Walk items front-to-back, dropping turns until we've removed enough.
        # Rich items are only kept once we've started keeping turns.
        keep_items: list[tuple[str, object]] = []
        remaining_to_drop = omitted
        started_keeping = False
        for kind, data in items:
            if kind == "turns":
                if remaining_to_drop >= len(data):
                    remaining_to_drop -= len(data)
                    continue  # drop entire chunk
                elif remaining_to_drop > 0:
                    data = data[remaining_to_drop:]  # type: ignore[index]
                    remaining_to_drop = 0
                started_keeping = True
                keep_items.append((kind, data))
            else:
                # Keep rich items only after we've started keeping turns
                if started_keeping:
                    keep_items.append((kind, data))
        items = keep_items

    # Second pass: assign header/footer flags so rule bars appear exactly once
    turn_indices = [i for i, (k, _) in enumerate(items) if k == "turns"]
    if not turn_indices:
        return []
    first_tc, last_tc = turn_indices[0], turn_indices[-1]
    short_id = session_id[:8]

    outputs: list = []
    for i, (kind, data) in enumerate(items):
        if kind == "turns":
            outputs.append(
                HistoryReplay(
                    turns=data,  # type: ignore[arg-type]
                    session_id=short_id if i == first_tc else "",
                    show_header=(i == first_tc),
                    show_footer=(i == last_tc),
                    omitted_count=omitted if i == first_tc else 0,
                )
            )
        else:
            outputs.append(_RichReplayPayload(payload=data))  # type: ignore[arg-type]

    return outputs
