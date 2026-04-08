"""HTTP-layer tests for the journal endpoints in main.py.

Uses Starlette TestClient to exercise the full FastAPI request/response cycle,
including input validation (400), session-existence checks (404), and happy paths.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from nemo_oo_agents_viewer import main as main_module
from nemo_oo_agents_viewer import otlp_store
from nemo_oo_agents_viewer.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE msg_content (
            hash TEXT PRIMARY KEY,
            msg  TEXT NOT NULL
        );

        CREATE TABLE llm_calls (
            call_id       TEXT PRIMARY KEY,
            session_id    TEXT NOT NULL,
            span_id       TEXT,
            model         TEXT,
            ts_start      REAL,
            ts_end        REAL,
            input_hashes  TEXT NOT NULL,
            output_hashes TEXT NOT NULL,
            tokens        TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_span ON llm_calls(span_id);

        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            experiment TEXT NOT NULL,
            span_count INTEGER DEFAULT 0,
            modified REAL DEFAULT 0,
            resource_attrs TEXT,
            eval_passed INTEGER,
            eval_metadata TEXT
        );

        CREATE TABLE spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            trace_id TEXT,
            span_id TEXT,
            parent_span_id TEXT,
            name TEXT,
            kind INTEGER,
            start_time_ns INTEGER,
            end_time_ns INTEGER,
            status_code INTEGER,
            status_message TEXT,
            attributes TEXT,
            resource TEXT,
            events TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id);

        CREATE TABLE IF NOT EXISTS annotations (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            span_id TEXT,
            target TEXT,
            name TEXT NOT NULL,
            score REAL,
            label TEXT,
            comment TEXT,
            tags TEXT,
            created_at TEXT NOT NULL,
            author_id TEXT,
            source TEXT NOT NULL DEFAULT 'human',
            metadata TEXT
        );
    """)
    db.commit()
    return db


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient backed by a fresh temporary DB.

    Redirects ``DB_PATH`` to a temp file so the lifespan's ``init_db()``
    creates a clean schema without touching the production ``traces.db``.
    Resets ``_db`` to ``None`` so ``init_db()`` re-creates it at the new path.

    After startup, patches the thread-local read/write connections to use
    the same ``_db`` connection so writes from the async handler (event loop
    thread) are visible to reads from the test thread without WAL lag.
    """
    monkeypatch.setattr(otlp_store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(otlp_store, "_db", None)
    # Replace the module-level write executor with a fresh one so that the
    # lifespan shutdown (which calls .shutdown()) doesn't poison other tests.
    monkeypatch.setattr(
        main_module,
        "_write_executor",
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-writer-test"),
    )
    # Clear any stale thread-local connections
    if hasattr(otlp_store._read_tls, "conn"):
        del otlp_store._read_tls.conn
    if hasattr(otlp_store._write_tls, "conn"):
        del otlp_store._write_tls.conn
    with TestClient(app) as c:
        # After init_db(), share the _db connection across all threads
        db = otlp_store._db
        otlp_store._read_tls.conn = db
        otlp_store._write_tls.conn = db
        yield c
    # Clean up thread-local state
    if hasattr(otlp_store._read_tls, "conn"):
        del otlp_store._read_tls.conn
    if hasattr(otlp_store._write_tls, "conn"):
        del otlp_store._write_tls.conn


def _seed_session(db: sqlite3.Connection, session_id: str = "sess1") -> None:
    db.execute(
        "INSERT INTO sessions (session_id, experiment, span_count, modified) VALUES (?, 'default', 0, 0)",
        (session_id,),
    )
    db.commit()


# ---------------------------------------------------------------------------
# POST /v1/journal/messages
# ---------------------------------------------------------------------------


class TestJournalMessagesEndpoint:
    def test_happy_path_stores_messages(self, client):
        db = otlp_store._get_db()
        payload = [
            {"h": "sha256:aaa", "msg": {"role": "user", "content": "hi"}},
            {"h": "sha256:bbb", "msg": {"role": "assistant", "content": "hello"}},
        ]
        resp = client.post("/v1/journal/messages", json=payload)
        assert resp.status_code == 200
        rows = db.execute("SELECT hash FROM msg_content").fetchall()
        hashes = {r[0] for r in rows}
        assert "sha256:aaa" in hashes
        assert "sha256:bbb" in hashes

    def test_empty_list_returns_stored_zero(self, client):
        resp = client.post("/v1/journal/messages", json=[])
        assert resp.status_code == 200
        assert resp.json() == {"stored": 0}

    def test_dedup_idempotent(self, client):
        payload = [{"h": "sha256:dup", "msg": {"role": "user", "content": "x"}}]
        r1 = client.post("/v1/journal/messages", json=payload)
        r2 = client.post("/v1/journal/messages", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        db = otlp_store._get_db()
        count = db.execute("SELECT COUNT(*) FROM msg_content WHERE hash='sha256:dup'").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# POST /v1/journal/calls
# ---------------------------------------------------------------------------


class TestJournalCallsEndpoint:
    def test_happy_path(self, client):
        db = otlp_store._get_db()
        payload = {
            "call_id": "cid1",
            "session_id": "sess_x",
            "model": "gpt-4o",
            "ts_start": 1.0,
            "ts_end": 2.0,
            "input_hashes": [],
            "output_hashes": [],
        }
        resp = client.post("/v1/journal/calls", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        row = db.execute("SELECT call_id FROM llm_calls WHERE call_id='cid1'").fetchone()
        assert row is not None

    def test_missing_call_id_returns_400(self, client):
        resp = client.post(
            "/v1/journal/calls",
            json={"session_id": "s1", "input_hashes": [], "output_hashes": []},
        )
        assert resp.status_code == 400
        assert "call_id" in resp.json()["error"]

    def test_empty_string_call_id_returns_400(self, client):
        resp = client.post(
            "/v1/journal/calls",
            json={"call_id": "", "session_id": "s1", "input_hashes": [], "output_hashes": []},
        )
        assert resp.status_code == 400

    def test_missing_session_id_returns_400(self, client):
        resp = client.post(
            "/v1/journal/calls",
            json={"call_id": "cid2", "input_hashes": [], "output_hashes": []},
        )
        assert resp.status_code == 400
        assert "session_id" in resp.json()["error"]

    def test_non_dict_body_returns_400(self, client):
        resp = client.post("/v1/journal/calls", json=["not", "a", "dict"])
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/traces/{session_id}/calls
# ---------------------------------------------------------------------------


class TestGetSessionCallsEndpoint:
    def test_unknown_session_returns_404(self, client):
        resp = client.get("/api/traces/no-such-session/calls")
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    def test_known_session_with_no_calls_returns_200_empty(self, client):
        db = otlp_store._get_db()
        _seed_session(db, "sess_empty")
        resp = client.get("/api/traces/sess_empty/calls")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_known_session_with_calls_returns_records(self, client):
        db = otlp_store._get_db()
        _seed_session(db, "sess_calls")
        otlp_store.ingest_journal_messages(
            [{"h": "sha256:m1", "msg": {"role": "user", "content": "hi"}}]
        )
        otlp_store.ingest_journal_call(
            {
                "call_id": "c1",
                "session_id": "sess_calls",
                "model": "gpt-4o",
                "ts_start": 1.0,
                "ts_end": 2.0,
                "input_hashes": ["sha256:m1"],
                "output_hashes": [],
            }
        )
        resp = client.get("/api/traces/sess_calls/calls")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["call_id"] == "c1"
        assert data[0]["input_messages"] == [{"role": "user", "content": "hi"}]
