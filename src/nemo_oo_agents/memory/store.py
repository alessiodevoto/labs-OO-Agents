# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SQLite-centric memory store + pluggable vector index.

Everything lives in one SQLite file selected by ``MemoryConfig.path`` or the
embedding host. TUI defaults to one memory DB per session; other hosts may choose
workspace/project paths explicitly:

* ``memories``      — full record as JSON + promoted columns for filtering/sort
                      + the embedding as a float32 blob.
* ``memory_edges``  — the directed, typed association graph.

The vector index is abstracted behind ``VectorIndex`` so the brute-force numpy
default can later be swapped for ``sqlite-vec`` / Chroma without touching callers
(``docs/design/memory-system/design.md`` §4.5). The default ``NumpyVectorIndex``
does exact cosine KNN in memory over L2-normalised vectors.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from nemo_oo_agents.memory.schema import Edge, EdgeType, Memory, MemoryType
from nemo_oo_agents.memory.vector_backends import (
    NumpyVectorIndex,
    VectorIndex,
    make_vector_index,
)

if TYPE_CHECKING:
    from nemo_oo_agents.memory.config import VectorConfig


def _vec_to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    content       TEXT NOT NULL,
    importance    REAL NOT NULL DEFAULT 5.0,
    salience      REAL NOT NULL DEFAULT 0.0,
    strength      INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL,
    last_accessed REAL NOT NULL,
    access_count  INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0,
    embedding     BLOB,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_mem_archived ON memories(archived);

CREATE TABLE IF NOT EXISTS memory_edges (
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    type       TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    PRIMARY KEY (src, dst, type)
);
CREATE INDEX IF NOT EXISTS idx_edge_src ON memory_edges(src);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON memory_edges(dst);
"""


class MemoryStore:
    """Persistent store for memories + their association graph + vectors."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        vector_index: VectorIndex | None = None,
        vector_config: VectorConfig | None = None,
        embedding_dim: int | None = None,
    ) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # An explicit index wins; otherwise dispatch on the config's backend
        # (numpy / sqlite_vec / chroma_*). Default is the zero-dependency numpy index.
        if vector_index is not None:
            self._index: VectorIndex = vector_index
        elif vector_config is not None:
            self._index = make_vector_index(
                vector_config, dim=embedding_dim, conn=self._conn, path=self.path
            )
        else:
            self._index = NumpyVectorIndex()
        self._load_index()

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _load_index(self) -> None:
        cur = self._conn.execute(
            "SELECT id, embedding FROM memories WHERE archived = 0 AND embedding IS NOT NULL"
        )
        for row in cur.fetchall():
            self._index.add(row["id"], _blob_to_vec(row["embedding"]))

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        data = json.loads(row["data"])
        edges = [
            Edge(target_id=e["dst"], type=EdgeType(e["type"]), weight=e["weight"])
            for e in self._edge_rows(row["id"])
        ]
        data["edges"] = [e.model_dump(mode="json") for e in edges]
        # The promoted ``archived`` column is authoritative (set by archive()),
        # so it overrides whatever the serialised payload captured at write time.
        data["archived"] = bool(row["archived"])
        return Memory.model_validate(data)

    def _edge_rows(self, src: str) -> list[dict]:
        cur = self._conn.execute("SELECT dst, type, weight FROM memory_edges WHERE src = ?", (src,))
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def add(self, memory: Memory, embedding: np.ndarray | None = None) -> Memory:
        """Insert (or replace) a memory and its edges. Persists the embedding."""
        blob = _vec_to_blob(embedding) if embedding is not None else None
        payload = memory.model_dump(mode="json", exclude={"edges"})
        self._conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, type, content, importance, salience, strength, created_at,
                last_accessed, access_count, archived, embedding, data)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                memory.id,
                memory.type.value,
                memory.content,
                memory.importance,
                memory.salience,
                memory.strength,
                memory.created_at,
                memory.last_accessed_at,
                memory.access_count,
                int(memory.archived),
                blob,
                json.dumps(payload),
            ),
        )
        # Rewrite edges for this source.
        self._conn.execute("DELETE FROM memory_edges WHERE src = ?", (memory.id,))
        for e in memory.edges:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_edges (src, dst, type, weight, created_at)"
                " VALUES (?,?,?,?,?)",
                (memory.id, e.target_id, e.type.value, e.weight, e.created_at),
            )
        self._conn.commit()
        if embedding is not None and not memory.archived:
            self._index.add(memory.id, embedding)
        elif memory.archived:
            self._index.remove(memory.id)
        return memory

    def save(self, memory: Memory) -> None:
        """Persist mutations to an existing memory (keeps the stored embedding)."""
        payload = memory.model_dump(mode="json", exclude={"edges"})
        self._conn.execute(
            """UPDATE memories SET type=?, content=?, importance=?, salience=?,
               strength=?, last_accessed=?, access_count=?, archived=?, data=? WHERE id=?""",
            (
                memory.type.value,
                memory.content,
                memory.importance,
                memory.salience,
                memory.strength,
                memory.last_accessed_at,
                memory.access_count,
                int(memory.archived),
                json.dumps(payload),
                memory.id,
            ),
        )
        self._conn.execute("DELETE FROM memory_edges WHERE src = ?", (memory.id,))
        for e in memory.edges:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_edges (src, dst, type, weight, created_at)"
                " VALUES (?,?,?,?,?)",
                (memory.id, e.target_id, e.type.value, e.weight, e.created_at),
            )
        self._conn.commit()
        if memory.archived:
            self._index.remove(memory.id)

    def add_edge(
        self, src: str, dst: str, type: EdgeType = EdgeType.RELATED, weight: float = 1.0
    ) -> None:
        from nemo_oo_agents.memory.schema import _now

        self._conn.execute(
            "INSERT OR REPLACE INTO memory_edges (src, dst, type, weight, created_at)"
            " VALUES (?,?,?,?,?)",
            (src, dst, type.value, weight, _now()),
        )
        self._conn.commit()

    def archive(self, id: str) -> None:
        self._conn.execute("UPDATE memories SET archived = 1 WHERE id = ?", (id,))
        self._conn.commit()
        self._index.remove(id)

    def delete(self, id: str) -> None:
        self._conn.execute("DELETE FROM memories WHERE id = ?", (id,))
        self._conn.execute("DELETE FROM memory_edges WHERE src = ? OR dst = ?", (id, id))
        self._conn.commit()
        self._index.remove(id)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get(self, id: str) -> Memory | None:
        row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (id,)).fetchone()
        return self._row_to_memory(row) if row else None

    def get_embedding(self, id: str) -> np.ndarray | None:
        row = self._conn.execute("SELECT embedding FROM memories WHERE id = ?", (id,)).fetchone()
        if row and row["embedding"] is not None:
            return _blob_to_vec(row["embedding"])
        return None

    def neighbors(self, id: str) -> list[Edge]:
        return [
            Edge(target_id=e["dst"], type=EdgeType(e["type"]), weight=e["weight"])
            for e in self._edge_rows(id)
        ]

    def all_memories(self, *, include_archived: bool = False) -> list[Memory]:
        q = "SELECT * FROM memories" + ("" if include_archived else " WHERE archived = 0")
        return [self._row_to_memory(r) for r in self._conn.execute(q).fetchall()]

    def iter_memories(self, *, include_archived: bool = False) -> Iterator[Memory]:
        yield from self.all_memories(include_archived=include_archived)

    def count(self, *, include_archived: bool = False) -> int:
        q = "SELECT COUNT(*) AS n FROM memories" + (
            "" if include_archived else " WHERE archived = 0"
        )
        return int(self._conn.execute(q).fetchone()["n"])

    def knn(self, query_vec: np.ndarray, k: int) -> list[tuple[str, float]]:
        """Top-k by cosine similarity (excludes archived)."""
        return self._index.query(query_vec, k)

    def keyword_search(self, text: str, k: int, *, mem_type: MemoryType | None = None) -> list[str]:
        """Cheap sparse fallback: rank by overlapping-token LIKE matches."""
        from nemo_oo_agents.memory.embeddings import _TOKEN_RE

        tokens = list(dict.fromkeys(_TOKEN_RE.findall(text.lower())))[:12]
        if not tokens:
            return []
        scored: dict[str, int] = {}
        for tok in tokens:
            clause = "SELECT id FROM memories WHERE archived = 0 AND lower(content) LIKE ?"
            params: list[object] = [f"%{tok}%"]
            if mem_type is not None:
                clause += " AND type = ?"
                params.append(mem_type.value)
            for row in self._conn.execute(clause, params).fetchall():
                scored[row["id"]] = scored.get(row["id"], 0) + 1
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
        return [mid for mid, _ in ranked[:k]]

    def close(self) -> None:
        self._conn.close()
