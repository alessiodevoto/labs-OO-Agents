"""Unit tests for otlp_store — OTLP parsing and SQLite storage.

Tests are isolated via tmp_path: each test gets its own SQLite file so
there is no shared state between test functions.
"""

import sqlite3

import pytest

import nemo_oo_agents_viewer.otlp_store as store

# ---------------------------------------------------------------------------
# Fixture: isolated database per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point otlp_store at a fresh SQLite file for every test."""
    db_path = tmp_path / "test_traces.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    monkeypatch.setattr(store, "_db", None)
    store.init_db()
    yield
    if store._db:
        store._db.close()
    monkeypatch.setattr(store, "_db", None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_body(
    session_id: str = "sess-1",
    experiment: str = "exp-a",
    spans: list | None = None,
    resource_extra: dict | None = None,
    eval_attrs: dict | None = None,
) -> dict:
    """Build a minimal valid OTLP ExportTraceServiceRequest body."""
    res_attrs = [
        {"key": "session.id", "value": {"stringValue": session_id}},
        {"key": "experiment", "value": {"stringValue": experiment}},
    ]
    if resource_extra:
        for k, v in resource_extra.items():
            res_attrs.append({"key": k, "value": {"stringValue": v}})
    if eval_attrs:
        for k, v in eval_attrs.items():
            val = {"boolValue": v} if isinstance(v, bool) else {"stringValue": str(v)}
            res_attrs.append({"key": k, "value": val})

    default_span = {
        "traceId": "trace001",
        "spanId": "span001",
        "name": "agent.run",
        "kind": 1,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000001000000000",
        "attributes": [],
    }
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": res_attrs},
                "scopeSpans": [{"spans": spans if spans is not None else [default_span]}],
            }
        ]
    }


# ---------------------------------------------------------------------------
# otlp_attrs_to_dict
# ---------------------------------------------------------------------------


class TestOtlpAttrsToDict:
    def test_string_value(self):
        attrs = [{"key": "k", "value": {"stringValue": "hello"}}]
        assert store.otlp_attrs_to_dict(attrs) == {"k": "hello"}

    def test_int_value(self):
        attrs = [{"key": "n", "value": {"intValue": "42"}}]
        assert store.otlp_attrs_to_dict(attrs) == {"n": 42}

    def test_double_value(self):
        attrs = [{"key": "x", "value": {"doubleValue": 3.14}}]
        result = store.otlp_attrs_to_dict(attrs)
        assert abs(result["x"] - 3.14) < 1e-9

    def test_bool_value(self):
        attrs = [{"key": "flag", "value": {"boolValue": True}}]
        assert store.otlp_attrs_to_dict(attrs) == {"flag": True}

    def test_array_value(self):
        attrs = [
            {
                "key": "arr",
                "value": {
                    "arrayValue": {
                        "values": [
                            {"stringValue": "a"},
                            {"intValue": "2"},
                        ]
                    }
                },
            }
        ]
        assert store.otlp_attrs_to_dict(attrs) == {"arr": ["a", 2]}

    def test_kvlist_value(self):
        attrs = [
            {
                "key": "obj",
                "value": {
                    "kvlistValue": {
                        "values": [
                            {"key": "x", "value": {"stringValue": "y"}},
                        ]
                    }
                },
            }
        ]
        assert store.otlp_attrs_to_dict(attrs) == {"obj": {"x": "y"}}

    def test_bytes_value(self):
        attrs = [{"key": "b", "value": {"bytesValue": "AAEC"}}]
        assert store.otlp_attrs_to_dict(attrs) == {"b": "AAEC"}

    def test_unknown_value_type_skipped(self):
        attrs = [{"key": "k", "value": {"unknownType": "x"}}]
        assert store.otlp_attrs_to_dict(attrs) == {}

    def test_empty_list(self):
        assert store.otlp_attrs_to_dict([]) == {}

    def test_multiple_attrs(self):
        attrs = [
            {"key": "a", "value": {"stringValue": "1"}},
            {"key": "b", "value": {"intValue": "2"}},
        ]
        assert store.otlp_attrs_to_dict(attrs) == {"a": "1", "b": 2}


# ---------------------------------------------------------------------------
# _extract_session_and_experiment
# ---------------------------------------------------------------------------


class TestExtractSessionAndExperiment:
    def test_extracts_both_fields(self):
        body = _make_body(session_id="abc", experiment="my-exp")
        sid, exp = store._extract_session_and_experiment(body)
        assert sid == "abc"
        assert exp == "my-exp"

    def test_defaults_experiment_to_default(self):
        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "session.id", "value": {"stringValue": "s1"}}]
                    },
                    "scopeSpans": [],
                }
            ]
        }
        sid, exp = store._extract_session_and_experiment(body)
        assert sid == "s1"
        assert exp == "default"

    def test_no_session_id_returns_empty_string(self):
        body = {"resourceSpans": []}
        sid, exp = store._extract_session_and_experiment(body)
        assert sid == ""
        assert exp == "default"

    def test_fallback_to_span_attributes_when_resource_has_no_session(self):
        """BatchSpanProcessor exports without resource session.id; fall back to span attrs."""
        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "experiment", "value": {"stringValue": "exp-batch"}}
                            # No session.id in resource attrs — simulates BSP thread context issue
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "t1",
                                    "spanId": "s1",
                                    "name": "method.run",
                                    "attributes": [
                                        {
                                            "key": "session.id",
                                            "value": {"stringValue": "sess-from-span"},
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        sid, exp = store._extract_session_and_experiment(body)
        assert sid == "sess-from-span"
        assert exp == "exp-batch"

    def test_resource_session_takes_priority_over_span_session(self):
        """Resource session.id wins when both are present."""
        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "session.id", "value": {"stringValue": "resource-session"}},
                            {"key": "experiment", "value": {"stringValue": "exp-x"}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "t1",
                                    "spanId": "s1",
                                    "name": "span",
                                    "attributes": [
                                        {
                                            "key": "session.id",
                                            "value": {"stringValue": "span-session"},
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        sid, exp = store._extract_session_and_experiment(body)
        assert sid == "resource-session"
        assert exp == "exp-x"

    def test_fallback_preserves_experiment_from_resource(self):
        """experiment comes from resource attrs even when session.id comes from spans."""
        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "experiment", "value": {"stringValue": "my-exp"}}]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "spanId": "s1",
                                    "attributes": [
                                        {"key": "session.id", "value": {"stringValue": "s-123"}}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        sid, exp = store._extract_session_and_experiment(body)
        assert sid == "s-123"
        assert exp == "my-exp"


# ---------------------------------------------------------------------------
# _extract_eval_fields
# ---------------------------------------------------------------------------


class TestExtractEvalFields:
    def test_eval_passed_true_from_resource(self):
        body = _make_body(eval_attrs={"eval.passed": True})
        eval_passed, meta = store._extract_eval_fields(body)
        assert eval_passed == 1
        assert "passed" not in meta  # dedicated column, not in metadata blob

    def test_eval_passed_false(self):
        body = _make_body(eval_attrs={"eval.passed": False})
        eval_passed, _ = store._extract_eval_fields(body)
        assert eval_passed == 0

    def test_other_eval_fields_go_to_meta(self):
        body = _make_body(eval_attrs={"eval.model": "gpt-4", "eval.tier": "hard"})
        _, meta = store._extract_eval_fields(body)
        assert meta["model"] == "gpt-4"
        assert meta["tier"] == "hard"

    def test_no_eval_fields(self):
        body = _make_body()
        eval_passed, meta = store._extract_eval_fields(body)
        assert eval_passed is None
        assert meta == {}

    def test_eval_passed_from_root_span(self):
        """eval.passed on a root span (no parentSpanId) should be picked up."""
        root_span = {
            "traceId": "t1",
            "spanId": "s1",
            "name": "root",
            "kind": 1,
            "startTimeUnixNano": "0",
            "endTimeUnixNano": "1",
            "attributes": [{"key": "eval.passed", "value": {"boolValue": True}}],
        }
        body = _make_body(spans=[root_span])
        eval_passed, _ = store._extract_eval_fields(body)
        assert eval_passed == 1

    def test_non_root_span_eval_fields_ignored(self):
        """eval fields on child spans should NOT be extracted."""
        child_span = {
            "traceId": "t1",
            "spanId": "s2",
            "parentSpanId": "s1",  # has a parent → not root
            "name": "child",
            "kind": 1,
            "startTimeUnixNano": "0",
            "endTimeUnixNano": "1",
            "attributes": [{"key": "eval.passed", "value": {"boolValue": True}}],
        }
        body = _make_body(spans=[child_span])
        eval_passed, _ = store._extract_eval_fields(body)
        assert eval_passed is None


# ---------------------------------------------------------------------------
# _parse_eval_metadata
# ---------------------------------------------------------------------------


class TestParseEvalMetadata:
    def test_valid_json(self):
        assert store._parse_eval_metadata('{"a": 1}') == {"a": 1}

    def test_none_returns_empty(self):
        assert store._parse_eval_metadata(None) == {}

    def test_empty_string_returns_empty(self):
        assert store._parse_eval_metadata("") == {}

    def test_corrupt_json_returns_empty(self):
        assert store._parse_eval_metadata("{bad json}") == {}


# ---------------------------------------------------------------------------
# ingest — new session
# ---------------------------------------------------------------------------


class TestIngestNewSession:
    def test_creates_session_row(self):
        store.ingest(_make_body(session_id="s1", experiment="e1"))
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == "s1"
        assert sessions[0]["experiment"] == "e1"

    def test_stores_spans(self):
        store.ingest(_make_body(session_id="s1"))
        spans = store.get_session_spans("s1")
        assert len(spans) == 1
        assert spans[0]["name"] == "agent.run"

    def test_span_count_matches(self):
        spans = [
            {
                "traceId": f"t{i}",
                "spanId": f"s{i}",
                "name": f"span{i}",
                "kind": 1,
                "startTimeUnixNano": "0",
                "endTimeUnixNano": "1",
                "attributes": [],
            }
            for i in range(5)
        ]
        result = store.ingest(_make_body(session_id="s1", spans=spans))
        assert result["span_count"] == 5
        sessions = store.list_sessions()
        assert sessions[0]["span_count"] == 5

    def test_returns_correct_dict(self):
        result = store.ingest(_make_body(session_id="s1", experiment="e1"))
        assert result == {"session_id": "s1", "experiment": "e1", "span_count": 1}

    def test_unknown_session_id_fallback(self):
        """Body with no session.id gets an unknown_* ID."""
        body = {"resourceSpans": []}
        result = store.ingest(body)
        assert result["session_id"].startswith("unknown_")

    def test_eval_fields_stored(self):
        body = _make_body(eval_attrs={"eval.passed": True, "eval.model": "claude"})
        store.ingest(body)
        sessions = store.list_sessions()
        assert sessions[0]["eval"]["passed"] is True
        assert sessions[0]["eval"]["model"] == "claude"

    def test_empty_spans_list(self):
        result = store.ingest(_make_body(session_id="s1", spans=[]))
        assert result["span_count"] == 0


# ---------------------------------------------------------------------------
# ingest — re-ingest (session merge)
# ---------------------------------------------------------------------------


class TestIngestReIngest:
    def test_span_count_accumulates(self):
        store.ingest(_make_body(session_id="s1"))
        store.ingest(_make_body(session_id="s1"))
        sessions = store.list_sessions()
        assert sessions[0]["span_count"] == 2

    def test_eval_metadata_merges(self):
        body1 = _make_body(eval_attrs={"eval.model": "claude"})
        body2 = _make_body(eval_attrs={"eval.tier": "hard"})
        store.ingest(body1)
        store.ingest(body2)
        sessions = store.list_sessions()
        assert sessions[0]["eval"]["model"] == "claude"
        assert sessions[0]["eval"]["tier"] == "hard"

    def test_later_eval_passed_overwrites(self):
        store.ingest(_make_body(eval_attrs={"eval.passed": False}))
        store.ingest(_make_body(eval_attrs={"eval.passed": True}))
        sessions = store.list_sessions()
        assert sessions[0]["eval"]["passed"] is True

    def test_corrupt_existing_metadata_handled_gracefully(self):
        """If eval_metadata in DB is corrupt JSON, re-ingest should not crash."""
        store.ingest(_make_body(session_id="s1", experiment="e1"))
        # Corrupt the stored metadata directly
        db = store._get_db()
        db.execute("UPDATE sessions SET eval_metadata = ? WHERE session_id = ?", ("{bad", "s1"))
        db.commit()
        # Re-ingest should not raise
        store.ingest(_make_body(session_id="s1", eval_attrs={"eval.tier": "easy"}))
        sessions = store.list_sessions()
        assert sessions[0]["eval"]["tier"] == "easy"


# ---------------------------------------------------------------------------
# list_sessions / list_experiments filters
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_filter_by_experiment(self):
        store.ingest(_make_body(session_id="s1", experiment="exp-a"))
        store.ingest(_make_body(session_id="s2", experiment="exp-b"))
        results = store.list_sessions(experiment="exp-a")
        assert len(results) == 1
        assert results[0]["id"] == "s1"

    def test_eval_only_filter(self):
        store.ingest(_make_body(session_id="s1"))  # no eval
        store.ingest(_make_body(session_id="s2", eval_attrs={"eval.model": "x"}))
        results = store.list_sessions(eval_only=True)
        assert len(results) == 1
        assert results[0]["id"] == "s2"

    def test_list_experiments(self):
        store.ingest(_make_body(session_id="s1", experiment="alpha"))
        store.ingest(_make_body(session_id="s2", experiment="beta"))
        store.ingest(_make_body(session_id="s3", experiment="alpha"))
        experiments = store.list_experiments()
        assert sorted(experiments) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# get_session_spans
# ---------------------------------------------------------------------------


class TestGetSessionSpans:
    def test_returns_spans_in_start_time_order(self):
        spans = [
            {
                "traceId": "t",
                "spanId": "s2",
                "name": "second",
                "kind": 1,
                "startTimeUnixNano": "2000",
                "endTimeUnixNano": "3000",
                "attributes": [],
            },
            {
                "traceId": "t",
                "spanId": "s1",
                "name": "first",
                "kind": 1,
                "startTimeUnixNano": "1000",
                "endTimeUnixNano": "2000",
                "attributes": [],
            },
        ]
        store.ingest(_make_body(session_id="s1", spans=spans))
        result = store.get_session_spans("s1")
        assert result[0]["name"] == "first"
        assert result[1]["name"] == "second"

    def test_raises_for_missing_session(self):
        with pytest.raises(FileNotFoundError):
            store.get_session_spans("nonexistent")

    def test_parent_span_id_included(self):
        spans = [
            {
                "traceId": "t",
                "spanId": "child",
                "parentSpanId": "parent",
                "name": "child-span",
                "kind": 1,
                "startTimeUnixNano": "0",
                "endTimeUnixNano": "1",
                "attributes": [],
            },
        ]
        store.ingest(_make_body(session_id="s1", spans=spans))
        result = store.get_session_spans("s1")
        assert result[0]["parentSpanId"] == "parent"


# ---------------------------------------------------------------------------
# delete functions
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_session_removes_session_and_spans(self):
        store.ingest(_make_body(session_id="s1"))
        deleted = store.delete_session("s1")
        assert deleted is True
        assert store.list_sessions() == []
        with pytest.raises(FileNotFoundError):
            store.get_session_spans("s1")

    def test_delete_session_returns_false_if_not_found(self):
        assert store.delete_session("ghost") is False

    def test_delete_all_sessions(self):
        store.ingest(_make_body(session_id="s1"))
        store.ingest(_make_body(session_id="s2"))
        store.delete_all_sessions()
        assert store.list_sessions() == []
        stats = store.get_stats()
        assert stats["sessions"] == 0
        assert stats["spans"] == 0


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_counts_sessions_spans_experiments(self):
        store.ingest(_make_body(session_id="s1", experiment="e1"))
        store.ingest(_make_body(session_id="s2", experiment="e2"))
        stats = store.get_stats()
        assert stats["sessions"] == 2
        assert stats["spans"] == 2
        assert stats["experiments"] == 2


# ---------------------------------------------------------------------------
# v1 → v2 migration
# ---------------------------------------------------------------------------


class TestMigrateV1ToV2:
    def test_migrates_old_columns_to_json_blob(self, tmp_path, monkeypatch):
        """A v1 database with separate eval_* columns is migrated correctly."""
        db_path = tmp_path / "v1.db"
        monkeypatch.setattr(store, "DB_PATH", db_path)
        monkeypatch.setattr(store, "_db", None)

        # Build a v1 schema manually
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                experiment TEXT NOT NULL,
                span_count INTEGER DEFAULT 0,
                modified REAL DEFAULT 0,
                resource_attrs TEXT,
                eval_passed INTEGER,
                eval_model TEXT,
                eval_test_name TEXT,
                eval_score REAL,
                eval_scores TEXT,
                eval_input TEXT,
                eval_output TEXT,
                eval_expected TEXT,
                eval_tier TEXT,
                eval_error TEXT,
                eval_input_tokens INTEGER,
                eval_output_tokens INTEGER,
                eval_agent_class TEXT,
                eval_method TEXT,
                eval_variant TEXT,
                eval_run_id TEXT,
                eval_display_name TEXT,
                eval_trace_file TEXT,
                eval_duration_seconds REAL
            );
            CREATE TABLE spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                trace_id TEXT, span_id TEXT, parent_span_id TEXT,
                name TEXT, kind INTEGER, start_time_ns INTEGER, end_time_ns INTEGER,
                status_code INTEGER, status_message TEXT,
                attributes TEXT, resource TEXT, events TEXT
            );
            CREATE TABLE annotations (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, span_id TEXT,
                target TEXT, name TEXT NOT NULL, score REAL, label TEXT, comment TEXT,
                tags TEXT, created_at TEXT NOT NULL, author_id TEXT,
                source TEXT NOT NULL DEFAULT 'human', metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id);
            CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id);
            CREATE INDEX IF NOT EXISTS idx_annotations_span ON annotations(session_id, span_id);
        """)
        conn.execute(
            """INSERT INTO sessions
               (session_id, experiment, span_count, modified, eval_passed,
                eval_model, eval_test_name, eval_score, eval_scores, eval_tier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("s1", "exp-v1", 3, 1.0, 1, "claude-3", "test_foo", 0.9, '["score_a"]', "easy"),
        )
        conn.commit()
        conn.close()

        # init_db should detect eval_model column and migrate
        store.init_db()

        sessions = store.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s["id"] == "s1"
        assert s["eval"]["passed"] is True
        assert s["eval"]["model"] == "claude-3"
        assert s["eval"]["test_name"] == "test_foo"
        assert s["eval"]["tier"] == "easy"
        # eval_scores is in json_parse_cols — should be parsed from string
        assert s["eval"]["scores"] == ["score_a"]

    def test_migrates_session_without_eval_passed(self, tmp_path, monkeypatch):
        """Sessions without eval_passed migrate cleanly (NULL → None)."""
        db_path = tmp_path / "v1_no_eval.db"
        monkeypatch.setattr(store, "DB_PATH", db_path)
        monkeypatch.setattr(store, "_db", None)

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, experiment TEXT NOT NULL,
                span_count INTEGER DEFAULT 0, modified REAL DEFAULT 0,
                resource_attrs TEXT, eval_passed INTEGER, eval_model TEXT,
                eval_test_id TEXT, eval_test_name TEXT, eval_tier TEXT,
                eval_score REAL, eval_error TEXT, eval_input_tokens INTEGER,
                eval_output_tokens INTEGER, eval_agent_class TEXT, eval_method TEXT,
                eval_variant TEXT, eval_run_id TEXT, eval_display_name TEXT,
                eval_scores TEXT, eval_input TEXT, eval_output TEXT,
                eval_expected TEXT, eval_trace_file TEXT, eval_duration_seconds REAL
            );
            CREATE TABLE spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                trace_id TEXT, span_id TEXT, parent_span_id TEXT, name TEXT,
                kind INTEGER, start_time_ns INTEGER, end_time_ns INTEGER,
                status_code INTEGER, status_message TEXT, attributes TEXT,
                resource TEXT, events TEXT
            );
            CREATE TABLE annotations (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, span_id TEXT,
                target TEXT, name TEXT NOT NULL, score REAL, label TEXT,
                comment TEXT, tags TEXT, created_at TEXT NOT NULL, author_id TEXT,
                source TEXT NOT NULL DEFAULT 'human', metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id);
            CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id);
            CREATE INDEX IF NOT EXISTS idx_annotations_span ON annotations(session_id, span_id);
        """)
        conn.execute(
            "INSERT INTO sessions (session_id, experiment, span_count, modified) VALUES (?, ?, ?, ?)",
            ("s2", "exp-plain", 1, 0.0),
        )
        conn.commit()
        conn.close()

        store.init_db()
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == "s2"
        # No eval fields → no eval key in dict
        assert "eval" not in sessions[0]
