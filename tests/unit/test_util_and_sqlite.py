"""Unit tests for util/ package and storage/sqlite.py."""

from __future__ import annotations

import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# util/__init__.py
# ---------------------------------------------------------------------------


class TestUtilInit:
    """Tests for nemo_oo_agents.util.__init__."""

    def test_logger_is_exported(self):
        from nemo_oo_agents import util

        assert hasattr(util, "logger")

    def test_all_contains_logger(self):
        import nemo_oo_agents.util as util_module

        assert "logger" in util_module.__all__

    def test_logger_has_expected_functions(self):
        from nemo_oo_agents.util import logger

        assert callable(logger.debug)
        assert callable(logger.info)
        assert callable(logger.warning)
        assert callable(logger.error)


# ---------------------------------------------------------------------------
# util/_context.py
# ---------------------------------------------------------------------------


class TestContext:
    """Tests for nemo_oo_agents.util._context."""

    def setup_method(self):
        """Reset context var before each test."""
        from nemo_oo_agents.util._context import _current_agent_var

        # Reset to None between tests
        _current_agent_var.set(None)

    def test_set_and_get_current_agent(self):
        from nemo_oo_agents.util._context import _current_agent, _set_current_agent

        fake_agent = MagicMock()
        _set_current_agent(fake_agent)
        assert _current_agent() is fake_agent

    def test_current_agent_raises_when_not_set(self):
        from nemo_oo_agents.util._context import _current_agent

        with pytest.raises(RuntimeError, match="No agent in context"):
            _current_agent()

    def test_set_current_agent_overwrites_previous(self):
        from nemo_oo_agents.util._context import _current_agent, _set_current_agent

        agent1 = MagicMock(name="agent1")
        agent2 = MagicMock(name="agent2")
        _set_current_agent(agent1)
        _set_current_agent(agent2)
        assert _current_agent() is agent2

    def test_set_current_agent_to_none_raises_on_get(self):
        from nemo_oo_agents.util._context import _current_agent, _set_current_agent

        fake_agent = MagicMock()
        _set_current_agent(fake_agent)
        _set_current_agent(None)
        with pytest.raises(RuntimeError):
            _current_agent()

    def test_runtime_var_exists(self):
        """The _current_runtime_var ContextVar is also exported."""
        from nemo_oo_agents.util._context import _current_runtime_var

        assert _current_runtime_var is not None


# ---------------------------------------------------------------------------
# util/logger.py
# ---------------------------------------------------------------------------


class TestLogger:
    """Tests for nemo_oo_agents.util.logger."""

    def setup_method(self):
        """Reset context var and set a fake agent before each test."""
        from nemo_oo_agents.util._context import _current_agent_var

        self.fake_agent = MagicMock()
        self.fake_agent.__class__.__name__ = "FakeAgent"
        _current_agent_var.set(self.fake_agent)

    def teardown_method(self):
        from nemo_oo_agents.util._context import _current_agent_var

        _current_agent_var.set(None)

    def test_debug_calls_python_logger(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.debug") as mock_debug:
            logger.debug("test debug message", key="value")
            mock_debug.assert_called_once_with("test debug message", extra={"key": "value"})

    def test_info_calls_python_logger(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.info") as mock_info:
            logger.info("test info message", count=42)
            mock_info.assert_called_once_with("test info message", extra={"count": 42})

    def test_warning_calls_python_logger(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.warning") as mock_warning:
            logger.warning("test warning", level=90)
            mock_warning.assert_called_once_with("test warning", extra={"level": 90})

    def test_error_calls_python_logger(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.error") as mock_error:
            logger.error("test error", err_type="ValueError")
            mock_error.assert_called_once_with("test error", extra={"err_type": "ValueError"})

    def test_logger_name_uses_agent_class(self):
        """Logger name should be 'agent.<ClassName>'."""
        from nemo_oo_agents.util import logger

        captured = []

        original_get_logger = logging.getLogger

        def mock_get_logger(name):
            captured.append(name)
            return original_get_logger(name)

        with patch("logging.getLogger", side_effect=mock_get_logger):
            logger.info("hello")

        assert any("FakeAgent" in name for name in captured)

    def test_debug_no_kwargs(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.debug") as mock_debug:
            logger.debug("no extras")
            mock_debug.assert_called_once_with("no extras", extra={})

    def test_info_no_kwargs(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.info") as mock_info:
            logger.info("no extras")
            mock_info.assert_called_once_with("no extras", extra={})

    def test_warning_no_kwargs(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.warning") as mock_warning:
            logger.warning("no extras")
            mock_warning.assert_called_once_with("no extras", extra={})

    def test_error_no_kwargs(self):
        from nemo_oo_agents.util import logger

        with patch("logging.Logger.error") as mock_error:
            logger.error("no extras")
            mock_error.assert_called_once_with("no extras", extra={})

    def test_logger_raises_when_no_agent(self):
        from nemo_oo_agents.util import logger
        from nemo_oo_agents.util._context import _current_agent_var

        _current_agent_var.set(None)
        with pytest.raises(RuntimeError, match="No agent in context"):
            logger.info("this should fail")


# ---------------------------------------------------------------------------
# util/prompt.py
# ---------------------------------------------------------------------------


class TestPreview:
    """Tests for prompt.preview()."""

    def setup_method(self):
        from nemo_oo_agents.util.prompt import preview

        self.preview = preview

    def test_short_string_unchanged(self):
        assert self.preview("hello", max_tokens=500) == "hello"

    def test_long_string_truncated_beginning_end(self):
        """Text longer than max_chars * 2 gets beginning...end format."""
        # max_tokens=10 -> max_chars=40; text > 80 chars triggers begin...end
        text = "A" * 50 + "B" * 50  # 100 chars
        result = self.preview(text, max_tokens=10)
        assert "..." in result
        assert result.startswith("A")
        assert result.endswith("B")

    def test_moderately_long_string_truncated_with_ellipsis_at_end(self):
        """Text between max_chars and max_chars*2 gets text[:max_chars]..."""
        # max_tokens=10 -> max_chars=40; 50 chars > 40 but < 80
        text = "X" * 50
        result = self.preview(text, max_tokens=10)
        assert result.endswith("...")
        assert not result.startswith("...")

    def test_string_exactly_at_limit(self):
        text = "A" * 40
        result = self.preview(text, max_tokens=10)
        assert result == text  # exactly at limit, no truncation

    def test_list_converted_to_json(self):
        result = self.preview([1, 2, 3], max_tokens=500)
        assert result == "[1, 2, 3]"

    def test_dict_converted_to_json(self):
        result = self.preview({"key": "value"}, max_tokens=500)
        import json

        assert result == json.dumps({"key": "value"})

    def test_dict_long_truncated(self):
        big_dict = {"key": "value" * 1000}
        result = self.preview(big_dict, max_tokens=50)
        assert "..." in result

    def test_list_long_truncated(self):
        big_list = list(range(10000))
        result = self.preview(big_list, max_tokens=50)
        assert "..." in result

    def test_arbitrary_object_uses_str(self):
        class Obj:
            def __str__(self):
                return "custom_str"

        result = self.preview(Obj(), max_tokens=500)
        assert result == "custom_str"

    def test_integer_uses_str(self):
        result = self.preview(42, max_tokens=500)
        assert result == "42"

    def test_none_uses_str(self):
        result = self.preview(None, max_tokens=500)
        assert result == "None"

    def test_default_max_tokens(self):
        """Default max_tokens=500 -> max_chars=2000."""
        text = "x" * 1999
        result = self.preview(text)
        assert result == text  # fits in 2000 chars

    def test_json_serialization_failure_falls_back_to_str(self):
        """When json.dumps fails, falls back to str()."""
        # json is imported locally inside preview(), so we patch the json module itself

        from nemo_oo_agents.util.prompt import preview

        def failing_dumps(*args, **kwargs):
            raise ValueError("forced failure")

        with patch("json.dumps", side_effect=failing_dumps):
            result = preview([1, 2, 3], max_tokens=500)
        # Falls back to str([1, 2, 3])
        assert result == "[1, 2, 3]"


class TestTake:
    """Tests for prompt.take()."""

    def setup_method(self):
        from nemo_oo_agents.util.prompt import take

        self.take = take

    def test_list_returns_first_n(self):
        assert self.take([1, 2, 3, 4, 5], 3) == [1, 2, 3]

    def test_list_n_larger_than_list(self):
        assert self.take([1, 2, 3], 10) == [1, 2, 3]

    def test_tuple_returns_first_n(self):
        assert self.take((10, 20, 30, 40), 2) == [10, 20]

    def test_generator_returns_first_n(self):
        def gen():
            yield from range(100)

        assert self.take(gen(), 5) == [0, 1, 2, 3, 4]

    def test_generator_stops_early(self):
        """Generator returns first n items correctly."""

        def gen():
            yield from range(10)

        result = self.take(gen(), 3)
        assert result == [0, 1, 2]
        assert len(result) == 3

    def test_empty_list(self):
        assert self.take([], 5) == []

    def test_n_zero(self):
        assert self.take([1, 2, 3], 0) == []

    def test_range_iterable(self):
        assert self.take(range(100), 5) == [0, 1, 2, 3, 4]


class TestLast:
    """Tests for prompt.last()."""

    def setup_method(self):
        from nemo_oo_agents.util.prompt import last

        self.last = last

    def test_list_returns_last_n(self):
        assert self.last([1, 2, 3, 4, 5], 3) == [3, 4, 5]

    def test_list_n_larger_than_list(self):
        assert self.last([1, 2, 3], 10) == [1, 2, 3]

    def test_tuple_returns_last_n(self):
        assert self.last((10, 20, 30, 40), 2) == [30, 40]

    def test_generator_returns_last_n(self):
        def gen():
            yield from range(10)

        assert self.last(gen(), 5) == [5, 6, 7, 8, 9]

    def test_empty_list(self):
        assert self.last([], 3) == []

    def test_range_iterable(self):
        assert self.last(range(10), 5) == [5, 6, 7, 8, 9]

    def test_single_item(self):
        assert self.last([42], 1) == [42]

    def test_n_larger_than_generator(self):
        def gen():
            yield from [1, 2]

        assert self.last(gen(), 10) == [1, 2]


# ---------------------------------------------------------------------------
# util/quickstart.py — dataclasses only (module-level side effects are mocked)
# ---------------------------------------------------------------------------


class TestArtwork:
    """Tests for quickstart.Artwork."""

    def _make(self):
        # Import must be done under patch to avoid module-level side effects
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            return quickstart.Artwork("Starry Night", "van Gogh", 1_000_000.0)

    def test_init_attributes(self):
        artwork = self._make()
        assert artwork.title == "Starry Night"
        assert artwork.artist == "van Gogh"

    def test_get_appraisal(self):
        artwork = self._make()
        appraisal = artwork.get_appraisal()
        assert appraisal["title"] == "Starry Night"
        assert appraisal["artist"] == "van Gogh"
        assert appraisal["value"] == 1_000_000.0
        assert appraisal["currency"] == "USD"


class TestStockHolding:
    """Tests for quickstart.StockHolding."""

    def _make(self):
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            return quickstart.StockHolding("AAPL", 100, 150.0)

    def test_init_attributes(self):
        stock = self._make()
        assert stock.symbol == "AAPL"

    def test_get_total_value(self):
        stock = self._make()
        assert stock.get_total_value() == 15000.0


class TestJewelry:
    """Tests for quickstart.Jewelry."""

    def _make(self):
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            return quickstart.Jewelry("Diamond Ring", 2.5, 10_000.0)

    def test_init_attributes(self):
        jewelry = self._make()
        assert jewelry.description == "Diamond Ring"

    def test_compute_value(self):
        jewelry = self._make()
        assert jewelry.compute_value() == 25_000.0


class TestCollectible:
    """Tests for quickstart.Collectible."""

    def _make(self, condition="mint"):
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            return quickstart.Collectible("Baseball Card", 1000.0, condition)

    def test_init_attributes(self):
        c = self._make()
        assert c.name == "Baseball Card"
        assert c.condition == "mint"

    def test_estimate_value_mint(self):
        assert self._make("mint").estimate_value() == 1000.0

    def test_estimate_value_excellent(self):
        assert self._make("excellent").estimate_value() == pytest.approx(850.0)

    def test_estimate_value_good(self):
        assert self._make("good").estimate_value() == pytest.approx(700.0)

    def test_estimate_value_fair(self):
        assert self._make("fair").estimate_value() == pytest.approx(500.0)

    def test_estimate_value_unknown_condition(self):
        """Unknown condition defaults to 0.5 multiplier."""
        assert self._make("poor").estimate_value() == pytest.approx(500.0)


class TestAutorun:
    """Tests for quickstart.autorun decorator."""

    def test_autorun_calls_asyncio_run(self):
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            async def my_func():
                return 42

            with patch("asyncio.run") as mock_run, patch("builtins.print"):
                quickstart.autorun(my_func)
                assert mock_run.call_count == 1
                # The argument is a coroutine from calling my_func()
                call_arg = mock_run.call_args[0][0]
                import inspect

                assert inspect.iscoroutine(call_arg)
                call_arg.close()  # clean up the coroutine

    def test_autorun_returns_original_function(self):
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            async def my_func():
                return 42

            with patch("asyncio.run"), patch("builtins.print"):
                result = quickstart.autorun(my_func)
                assert result is my_func

    def test_autorun_prints_example_output(self):
        with (
            patch("dotenv.load_dotenv"),
            patch("nemo_oo_agents.unifiedllm.registry.get_llm_client"),
        ):
            from nemo_oo_agents.util import quickstart

            async def my_func():
                pass

            with patch("asyncio.run"), patch("builtins.print") as mock_print:
                quickstart.autorun(my_func)
                mock_print.assert_called_once_with("\n\nEXAMPLE OUTPUT:")


# ---------------------------------------------------------------------------
# storage/sqlite.py — SQLiteEventBackend
# ---------------------------------------------------------------------------


def _make_backend():
    """Create an in-memory SQLiteEventBackend for testing."""
    from nemo_oo_agents.storage.sqlite import SQLiteEventBackend, _ensure_schema

    conn = sqlite3.connect(":memory:")
    _ensure_schema(conn)
    return SQLiteEventBackend(conn), conn


class TestEnsureSchema:
    """Tests for _ensure_schema."""

    def test_creates_tables(self):
        from nemo_oo_agents.storage.sqlite import _ensure_schema

        conn = sqlite3.connect(":memory:")
        _ensure_schema(conn)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "events" in tables
        assert "active_tags" in tables
        assert "snapshots" in tables
        assert "schema_version" in tables

    def test_inserts_schema_version(self):
        from nemo_oo_agents.storage.sqlite import _SCHEMA_VERSION, _ensure_schema

        conn = sqlite3.connect(":memory:")
        _ensure_schema(conn)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == _SCHEMA_VERSION

    def test_schema_version_mismatch_raises(self):
        from nemo_oo_agents.storage.sqlite import _SCHEMA_VERSION, _ensure_schema

        conn = sqlite3.connect(":memory:")
        _ensure_schema(conn)
        # Tamper with the version
        conn.execute("UPDATE schema_version SET version = ?", (_SCHEMA_VERSION + 1,))
        conn.commit()
        with pytest.raises(RuntimeError, match="schema version mismatch"):
            _ensure_schema(conn)

    def test_idempotent_on_second_call(self):
        """Calling _ensure_schema twice on the same DB should not raise."""
        from nemo_oo_agents.storage.sqlite import _ensure_schema

        conn = sqlite3.connect(":memory:")
        _ensure_schema(conn)
        _ensure_schema(conn)  # should not raise


class TestSQLiteEventBackendStore:
    """Tests for SQLiteEventBackend.store and get."""

    def test_store_and_get_message(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="hello world")
        backend.store("tag1", msg)
        result = backend.get("tag1")
        assert result is not None
        assert result.content == "hello world"

    def test_store_adds_to_active_tags(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="test")
        backend.store("mytag", msg)
        assert "mytag" in backend.active_tags()

    def test_store_multiple_preserves_order(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        for i in range(5):
            backend.store(f"tag{i}", Message(content=f"msg{i}"))
        tags = backend.active_tags()
        assert tags == ["tag0", "tag1", "tag2", "tag3", "tag4"]

    def test_len_counts_events(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        assert len(backend) == 0
        backend.store("t1", Message(content="a"))
        assert len(backend) == 1
        backend.store("t2", Message(content="b"))
        assert len(backend) == 2

    def test_get_returns_none_for_missing(self):
        backend, _ = _make_backend()
        assert backend.get("nonexistent") is None

    def test_get_by_id(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="by_id_test")
        backend.store("tag_id", msg)
        result = backend.get_by_id(msg.id)
        assert result is not None
        assert result.content == "by_id_test"

    def test_get_by_id_returns_none_for_missing(self):
        backend, _ = _make_backend()
        assert backend.get_by_id("nonexistent-id") is None


class TestSQLiteEventBackendUpdate:
    """Tests for SQLiteEventBackend.update."""

    def test_update_field(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="original")
        backend.store("tag1", msg)
        ok = backend.update("tag1", content="updated")
        assert ok is True
        result = backend.get("tag1")
        assert result.content == "updated"

    def test_update_metadata(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="test")
        backend.store("tag1", msg)
        ok = backend.update("tag1", metadata={"key": "value"})
        assert ok is True
        result = backend.get("tag1")
        assert result.metadata.get("key") == "value"

    def test_update_returns_false_for_missing(self):
        backend, _ = _make_backend()
        assert backend.update("nonexistent", content="x") is False

    def test_update_nonexistent_field_is_ignored(self):
        """Updating a field that doesn't exist on the event is silently ignored."""
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="test")
        backend.store("tag1", msg)
        ok = backend.update("tag1", nonexistent_field="value")
        assert ok is True  # update succeeds even for unknown fields


class TestSQLiteEventBackendRemove:
    """Tests for SQLiteEventBackend.remove."""

    def test_remove_existing(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        backend.store("tag1", Message(content="test"))
        ok = backend.remove("tag1")
        assert ok is True
        assert backend.get("tag1") is None
        assert "tag1" not in backend.active_tags()

    def test_remove_nonexistent_returns_false(self):
        backend, _ = _make_backend()
        assert backend.remove("nonexistent") is False

    def test_remove_decrements_len(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        backend.store("t1", Message(content="a"))
        backend.store("t2", Message(content="b"))
        assert len(backend) == 2
        backend.remove("t1")
        assert len(backend) == 1


class TestSQLiteEventBackendSetStatus:
    """Tests for SQLiteEventBackend.set_status."""

    def test_set_status_to_archived(self):
        from nemo_oo_agents.context_blocks import EventStatus
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="test")
        backend.store("tag1", msg)
        ok = backend.set_status("tag1", EventStatus.ARCHIVED)
        assert ok is True
        result = backend.get("tag1")
        assert result.status == EventStatus.ARCHIVED

    def test_set_status_returns_false_for_missing(self):
        from nemo_oo_agents.context_blocks import EventStatus

        backend, _ = _make_backend()
        assert backend.set_status("nonexistent", EventStatus.ACTIVE) is False


class TestSQLiteEventBackendActiveTags:
    """Tests for active_tags, insert_active_tag, remove_active_tag."""

    def test_active_tags_empty(self):
        backend, _ = _make_backend()
        assert backend.active_tags() == []

    def test_insert_active_tag_at_position(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        backend.store("tag0", Message(content="a"))
        backend.store("tag2", Message(content="b"))
        # Insert a tag at position 1 (between existing positions)
        backend.insert_active_tag("tag1", 1)
        tags = backend.active_tags()
        assert tags.index("tag1") < tags.index("tag2")

    def test_remove_active_tag_existing(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        backend.store("tag1", Message(content="x"))
        ok = backend.remove_active_tag("tag1")
        assert ok is True
        assert "tag1" not in backend.active_tags()

    def test_remove_active_tag_nonexistent(self):
        backend, _ = _make_backend()
        assert backend.remove_active_tag("nonexistent") is False


class TestSQLiteEventBackendAllEvents:
    """Tests for all_events() iterator."""

    def test_all_events_empty(self):
        backend, _ = _make_backend()
        assert list(backend.all_events()) == []

    def test_all_events_in_insertion_order(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msgs = [Message(content=f"msg{i}") for i in range(3)]
        for i, m in enumerate(msgs):
            backend.store(f"tag{i}", m)
        events = list(backend.all_events())
        assert len(events) == 3
        assert events[0].content == "msg0"
        assert events[1].content == "msg1"
        assert events[2].content == "msg2"


class TestSQLiteEventBackendFindTag:
    """Tests for find_tag()."""

    def test_find_tag_existing(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        msg = Message(content="test")
        backend.store("mytag", msg)
        assert backend.find_tag(msg) == "mytag"

    def test_find_tag_nonexistent_returns_none(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        assert backend.find_tag(Message(content="x")) is None


class TestSQLiteEventBackendClear:
    """Tests for clear()."""

    def test_clear_removes_all_events_and_tags(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        for i in range(3):
            backend.store(f"tag{i}", Message(content=f"msg{i}"))
        backend.clear()
        assert len(backend) == 0
        assert backend.active_tags() == []

    def test_clear_resets_insertion_counter(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        backend.store("t1", Message(content="a"))
        backend.clear()
        # After clear, insertion counter resets to 0
        assert backend._insertion_counter == 0


class TestSQLiteEventBackendDeserialization:
    """Tests for _deserialize with unknown event types."""

    def test_unknown_event_type_falls_back_to_metadata(self, caplog):
        from nemo_oo_agents.context_blocks import Metadata

        backend, _ = _make_backend()
        import json

        raw = json.dumps(
            {
                "event_type": "totally_unknown_type_xyz",
                "id": "test-id",
                "status": "active",
            }
        )
        with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.storage.sqlite"):
            event = backend._deserialize(raw)
        assert isinstance(event, Metadata)
        assert "Unknown event_type" in caplog.text

    def test_known_event_type_uses_correct_class(self):
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        import json

        raw = json.dumps(
            {"event_type": "Message", "id": "test-id", "status": "active", "content": "hi"}
        )
        event = backend._deserialize(raw)
        assert isinstance(event, Message)
        assert event.content == "hi"


class TestSQLiteEventBackendRegisterEventType:
    """Tests for register_event_type()."""

    def test_register_new_type(self):
        from nemo_oo_agents.context_blocks import EventBase

        backend, _ = _make_backend()

        class MyEvent(EventBase):
            event_type: str = "my_custom_event"
            data: str = ""

        backend.register_event_type(MyEvent)
        assert backend._registry["my_custom_event"] is MyEvent

    def test_register_same_type_no_warning(self, caplog):
        """Re-registering the same class for same key should not warn."""
        from nemo_oo_agents.events import Message

        backend, _ = _make_backend()
        with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.storage.sqlite"):
            backend.register_event_type(Message)
        assert "overwrites" not in caplog.text

    def test_register_different_class_same_key_warns(self, caplog):
        """Registering a different class for an existing key should warn."""
        from nemo_oo_agents.context_blocks import EventBase

        backend, _ = _make_backend()

        class FakeMessage(EventBase):
            event_type: str = "Message"  # Same as Message!
            content: str = ""

        with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.storage.sqlite"):
            backend.register_event_type(FakeMessage)
        assert "overwrites" in caplog.text


class TestSQLiteStorageManager:
    """Tests for SQLiteStorageManager."""

    def test_in_memory_creation(self):
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        sm = SQLiteStorageManager(":memory:")
        assert sm.event_backend is not None
        sm.close()

    def test_file_based_creation(self, tmp_path):
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        db_path = tmp_path / "test.db"
        sm = SQLiteStorageManager(db_path)
        assert sm.event_backend is not None
        sm.close()
        assert db_path.exists()

    def test_context_manager(self):
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        with SQLiteStorageManager(":memory:") as sm:
            assert sm.event_backend is not None
        # After __exit__, connection is closed (further operations would fail)

    def test_get_latest_snapshot_id_empty(self):
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        sm = SQLiteStorageManager(":memory:")
        assert sm.get_latest_snapshot_id() is None
        sm.close()

    def test_get_latest_snapshot_created_at_empty(self):
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        sm = SQLiteStorageManager(":memory:")
        assert sm.get_latest_snapshot_created_at() is None
        sm.close()

    def test_event_backend_property(self):
        from nemo_oo_agents.runtime.event_backend import EventBackend
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        sm = SQLiteStorageManager(":memory:")
        assert isinstance(sm.event_backend, EventBackend)
        sm.close()

    def test_backend_uses_insertion_counter(self):
        """Insertion counter increments with each stored event."""
        from nemo_oo_agents.events import Message
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        sm = SQLiteStorageManager(":memory:")
        backend = sm._backend
        initial = backend._insertion_counter
        backend.store("t1", Message(content="a"))
        assert backend._insertion_counter == initial + 1
        backend.store("t2", Message(content="b"))
        assert backend._insertion_counter == initial + 2
        sm.close()

    def test_restore_snapshot_not_found_raises(self):
        from nemo_oo_agents.errors.storage import SnapshotNotFoundError
        from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

        sm = SQLiteStorageManager(":memory:")
        mock_agent = MagicMock()
        with pytest.raises(SnapshotNotFoundError, match="not found"):
            sm.restore_snapshot("nonexistent-snapshot-id", mock_agent)
        sm.close()
