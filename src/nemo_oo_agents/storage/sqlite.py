# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed StorageManager and EventBackend.

Provides persistent storage using stdlib sqlite3 — no new dependencies.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from context_blocks import EventBase, EventStatus, Metadata
from nemo_oo_agents.events import (
    AfterTurn,
    BeforeTurn,
    Error,
    Feedback,
    LLMOutput,
    Message,
    PythonOutput,
    Reasoning,
    Summary,
    Task,
)
from nemo_oo_agents.runtime.event_manager import EventManager
from nemo_oo_agents.storage.json_snapshot import snapshot_from_dict, snapshot_to_dict
from nemo_oo_agents.storage.snapshot import AgentSnapshot

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent

# Core event types pre-registered for deserialization.
_CORE_TYPES: dict[str, type[EventBase]] = {
    cls.model_fields["event_type"].default: cls  # type: ignore[index]
    for cls in (
        Task,
        Message,
        Reasoning,
        Error,
        Feedback,
        LLMOutput,
        PythonOutput,
        Summary,
        BeforeTurn,
        AfterTurn,
    )
}

_SCHEMA_VERSION = 1

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- event_type and status are denormalized from the JSON data blob for indexed
-- queries. All write paths must derive these columns from the blob to keep
-- them in sync. The JSON blob (data) is the source of truth.
CREATE TABLE IF NOT EXISTS events (
    tag             TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    data            TEXT NOT NULL,
    insertion_order INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id);

CREATE TABLE IF NOT EXISTS active_tags (
    position INTEGER NOT NULL,
    tag      TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    data        TEXT NOT NULL
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if needed and verify schema version."""
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))
        conn.commit()
    elif row[0] != _SCHEMA_VERSION:
        raise RuntimeError(
            f"SQLite schema version mismatch: DB has v{row[0]}, "
            f"code expects v{_SCHEMA_VERSION}. Migration required."
        )


class SQLiteEventBackend:
    """EventBackend backed by SQLite tables.

    Single-writer assumption: this backend assumes one process writes to the
    database at a time. The in-memory insertion counter and read-modify-write
    patterns in update/set_status are not safe under concurrent writers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._insertion_counter = self._max_insertion_order() + 1
        self._registry: dict[str, type[EventBase]] = dict(_CORE_TYPES)

    def register_event_type(self, cls: type[EventBase]) -> None:
        """Register a custom EventBase subclass for deserialization.

        Adds *cls* to the per-instance registry keyed by its ``event_type``
        default.  Logs a warning if the key already maps to a different class.
        """
        event_type = cls.model_fields["event_type"].default
        existing = self._registry.get(event_type)
        if existing is not None and existing is not cls:
            logger.warning(
                "register_event_type: %r overwrites existing %s for event_type %r",
                cls.__name__,
                existing.__name__,
                event_type,
            )
        self._registry[event_type] = cls

    def _max_insertion_order(self) -> int:
        row = self._conn.execute("SELECT MAX(insertion_order) FROM events").fetchone()
        return row[0] if row[0] is not None else -1

    def _max_position(self) -> int:
        row = self._conn.execute("SELECT MAX(position) FROM active_tags").fetchone()
        return row[0] if row[0] is not None else -1

    def _deserialize(self, data: str) -> EventBase:
        raw = json.loads(data)
        event_type = raw.get("event_type", "")
        cls = self._registry.get(event_type)
        if cls is None:
            logger.warning("Unknown event_type %r, falling back to Metadata", event_type)
            return Metadata.model_validate(raw)
        return cls.model_validate(raw)

    def _serialize(self, event: EventBase) -> str:
        return event.model_dump_json()

    # -- EventBackend protocol --

    def store(self, tag: str, event: EventBase) -> None:
        data = self._serialize(event)
        order = self._insertion_counter
        self._insertion_counter += 1
        with self._conn:
            self._conn.execute(
                "INSERT INTO events (tag, event_id, event_type, status, data, insertion_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tag, event.id, event.event_type, event.status.value, data, order),
            )
            pos = self._max_position() + 1
            self._conn.execute(
                "INSERT INTO active_tags (position, tag) VALUES (?, ?)",
                (pos, tag),
            )

    def get(self, tag: str) -> EventBase | None:
        row = self._conn.execute("SELECT data FROM events WHERE tag = ?", (tag,)).fetchone()
        if row is None:
            return None
        return self._deserialize(row[0])

    def get_by_id(self, event_id: str) -> EventBase | None:
        row = self._conn.execute(
            "SELECT data FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return self._deserialize(row[0])

    def update(self, tag: str, **fields: object) -> bool:
        row = self._conn.execute("SELECT data FROM events WHERE tag = ?", (tag,)).fetchone()
        if row is None:
            return False
        event = self._deserialize(row[0])
        for key, value in fields.items():
            if key == "metadata":
                event.metadata.update(value)  # type: ignore[arg-type]
            elif hasattr(event, key):
                setattr(event, key, value)
        new_data = self._serialize(event)
        with self._conn:
            self._conn.execute(
                "UPDATE events SET data = ?, status = ? WHERE tag = ?",
                (new_data, event.status.value, tag),
            )
        return True

    def remove(self, tag: str) -> bool:
        row = self._conn.execute("SELECT tag FROM events WHERE tag = ?", (tag,)).fetchone()
        if row is None:
            return False
        with self._conn:
            self._conn.execute("DELETE FROM events WHERE tag = ?", (tag,))
            self._conn.execute("DELETE FROM active_tags WHERE tag = ?", (tag,))
        return True

    def set_status(self, tag: str, status: EventStatus) -> bool:
        row = self._conn.execute("SELECT data FROM events WHERE tag = ?", (tag,)).fetchone()
        if row is None:
            return False
        event = self._deserialize(row[0])
        event.status = status
        new_data = self._serialize(event)
        with self._conn:
            self._conn.execute(
                "UPDATE events SET data = ?, status = ? WHERE tag = ?",
                (new_data, status.value, tag),
            )
        return True

    def active_tags(self) -> list[str]:
        rows = self._conn.execute("SELECT tag FROM active_tags ORDER BY position").fetchall()
        return [r[0] for r in rows]

    def insert_active_tag(self, tag: str, index: int) -> None:
        with self._conn:
            # Shift existing positions >= index up by 1
            self._conn.execute(
                "UPDATE active_tags SET position = position + 1 WHERE position >= ?",
                (index,),
            )
            self._conn.execute(
                "INSERT INTO active_tags (position, tag) VALUES (?, ?)",
                (index, tag),
            )

    def remove_active_tag(self, tag: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM active_tags WHERE tag = ?", (tag,))
        return cursor.rowcount > 0

    def all_events(self) -> Iterator[EventBase]:
        rows = self._conn.execute("SELECT data FROM events ORDER BY insertion_order").fetchall()
        for (data,) in rows:
            yield self._deserialize(data)

    def find_tag(self, event: EventBase) -> str | None:
        row = self._conn.execute(
            "SELECT tag FROM events WHERE event_id = ?", (event.id,)
        ).fetchone()
        if row is None:
            return None
        return row[0]

    def clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM active_tags")
        self._insertion_counter = 0

    def max_tag_num(self) -> int:
        # Extract the trailing number from each tag in SQL:
        #   - simple tags ("5")      → CAST("5" AS INTEGER) = 5
        #   - range tags ("2..40")   → substr after ".." → CAST("40" AS INTEGER) = 40
        # COALESCE handles the empty-table case where MAX returns NULL.
        row = self._conn.execute(
            """
            SELECT COALESCE(MAX(
                CAST(
                    CASE WHEN instr(tag, '..') > 0
                         THEN substr(tag, instr(tag, '..') + 2)
                         ELSE tag
                    END AS INTEGER
                )
            ), 0)
            FROM events
            """
        ).fetchone()
        return row[0]

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return row[0]


class SQLiteStorageManager:
    """StorageManager backed by a SQLite database.

    Provides persistent event storage and agent snapshots.
    Supports use as a context manager for safe resource cleanup.

    Security: Snapshot restore executes stored Python source code via
    ``exec()``. The database file must be treated as trusted input —
    an attacker who can modify it gains arbitrary code execution on
    restore. Protect the file with appropriate OS-level permissions.

    Args:
        db_path: Path to SQLite database file. Use ":memory:" for in-memory
                 (useful for testing).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(self._conn)
        self._backend = SQLiteEventBackend(self._conn)
        self._event_manager = EventManager(backend=self._backend)

    @property
    def event_manager(self) -> EventManager:
        return self._event_manager

    def save_snapshot(self, agent: Agent) -> str:
        snapshot = AgentSnapshot.from_agent(agent)
        data = snapshot_to_dict(snapshot)
        snapshot_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO snapshots (snapshot_id, created_at, data) VALUES (?, ?, ?)",
                (snapshot_id, created_at, json.dumps(data)),
            )
        return snapshot_id

    def restore_snapshot(self, snapshot_id: str, agent: Agent) -> None:
        from nemo_oo_agents.errors.storage import SnapshotNotFoundError

        row = self._conn.execute(
            "SELECT data FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(f"Snapshot '{snapshot_id}' not found")
        data = json.loads(row[0])
        snapshot = snapshot_from_dict(data)
        snapshot.restore(agent)

    def get_latest_snapshot_id(self) -> str | None:
        """Return the snapshot_id of the most recently saved snapshot, or None."""
        # created_at stores UTC ISO 8601 with +00:00 suffix; lexicographic sort is
        # chronological because all values share the same timezone representation.
        row = self._conn.execute(
            "SELECT snapshot_id FROM snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row is not None else None

    def get_latest_snapshot_created_at(self) -> datetime | None:
        """Return the creation timestamp of the most recent snapshot, or None."""
        # See get_latest_snapshot_id for the created_at sort assumption.
        row = self._conn.execute(
            "SELECT created_at FROM snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0])

    def restore_latest_snapshot(self, agent: Agent) -> bool:
        """Restore the most recent snapshot into agent.

        Args:
            agent: A freshly constructed Agent instance to restore into.

        Returns:
            True if a snapshot was found and restored, False if no snapshots exist.
        """
        snapshot_id = self.get_latest_snapshot_id()
        if snapshot_id is None:
            return False
        self.restore_snapshot(snapshot_id, agent)
        return True

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteStorageManager:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
