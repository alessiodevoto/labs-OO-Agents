"""Tests for the content-addressed message journal system.

Covers:
- MessageJournalCallback: hashing, delta sends, span_id capture, failure cleanup
- JournalExporter: SpanExporter interface, install/shutdown/idempotency
- exporters.journal() factory
- SpanLimits raised to 2048 in enable_tracing()
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from openinference_instrumentation_nemo_oo_agents._litellm_journal import (
    MessageJournalCallback,
    _hash_msg,
)

# ---------------------------------------------------------------------------
# _hash_msg
# ---------------------------------------------------------------------------


class TestHashMsg:
    def test_stable_across_dict_ordering(self):
        m1 = {"role": "user", "content": "hello"}
        m2 = {"content": "hello", "role": "user"}
        assert _hash_msg(m1) == _hash_msg(m2)

    def test_starts_with_sha256_prefix(self):
        assert _hash_msg({"role": "user", "content": "hi"}).startswith("sha256:")

    def test_null_content_differs_from_empty_string(self):
        assert _hash_msg({"role": "assistant", "content": None}) != _hash_msg({"role": "assistant", "content": ""})

    def test_different_tool_call_ids_differ(self):
        m1 = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_a", "function": {"name": "f", "arguments": "{}"}}],
        }
        m2 = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_b", "function": {"name": "f", "arguments": "{}"}}],
        }
        assert _hash_msg(m1) != _hash_msg(m2)

    def test_same_content_same_hash(self):
        m = {"role": "system", "content": "You are helpful."}
        assert _hash_msg(m) == _hash_msg(dict(m))

    def test_tool_role_with_tool_call_id(self):
        m1 = {"role": "tool", "tool_call_id": "call_x", "content": "result"}
        m2 = {"role": "tool", "tool_call_id": "call_y", "content": "result"}
        assert _hash_msg(m1) != _hash_msg(m2)


# ---------------------------------------------------------------------------
# MessageJournalCallback — delta sends
# ---------------------------------------------------------------------------


class TestMessageJournalCallbackDelta:
    @pytest.fixture
    def cb(self):
        cb = MessageJournalCallback("http://localhost:5001")
        # Patch out HTTP
        cb._messages_url = "mock://messages"
        cb._calls_url = "mock://calls"
        return cb

    def test_first_call_sends_all_messages(self, cb):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
        ):
            hashes = cb._send_new_messages("sess1", msgs)

        assert len(hashes) == 2
        assert len(posted) == 1
        sent_msgs = [item["msg"] for item in posted[0][1]]
        assert sent_msgs == msgs

    def test_second_call_with_same_messages_sends_nothing(self, cb):
        msgs = [{"role": "user", "content": "hello"}]
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append(payload) or True,
        ):
            cb._send_new_messages("sess1", msgs)
            posted.clear()
            cb._send_new_messages("sess1", msgs)

        assert posted == [], "no new messages should be sent on second call"

    def test_second_call_with_one_new_message_sends_only_new(self, cb):
        old = {"role": "user", "content": "hello"}
        new = {"role": "assistant", "content": "hi"}
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append(payload) or True,
        ):
            cb._send_new_messages("sess1", [old])
            posted.clear()
            cb._send_new_messages("sess1", [old, new])

        assert len(posted) == 1
        assert posted[0][0]["msg"] == new

    def test_sessions_are_isolated(self, cb):
        msg = {"role": "user", "content": "hello"}
        posted: dict[str, list] = {"sess1": [], "sess2": []}
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: (
                posted["sess1" if "sess1" in str(cb._sent) else "sess2"].append(payload) or True
            ),
        ):
            # send to sess1
            cb._send_new_messages("sess1", [msg])
            # same message to sess2 should also be sent (different session)
            p2 = []
            with patch(
                "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
                side_effect=lambda url, payload, **kw: p2.append(payload) or True,
            ):
                cb._send_new_messages("sess2", [msg])
        assert len(p2) == 1

    def test_concurrent_sends_are_thread_safe(self, cb):
        """Hammer _send_new_messages from multiple threads — no exceptions."""
        errors = []

        def send(i):
            try:
                with patch(
                    "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
                    return_value=True,
                ):
                    cb._send_new_messages("sess", [{"role": "user", "content": f"msg{i}"}])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=send, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# MessageJournalCallback — context block sideband
# ---------------------------------------------------------------------------


class TestContextBlockSideband:
    @pytest.fixture
    def cb(self):
        cb = MessageJournalCallback("http://localhost:5001")
        return cb

    def test_system_message_expanded_into_blocks(self, cb):
        """With sideband set, system message → compound entry + block sub-entries."""
        blocks = [
            "<persona>\nYou are helpful.\n</persona>",
            "<notes expr=\"self.context['notes']\">\nSome notes.\n</notes>",
        ]
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append(payload) or True,
        ):
            from openinference_instrumentation_nemo_oo_agents._context_sideband import set_context_blocks

            set_context_blocks(blocks)
            hashes = cb._send_new_messages(
                "sess1",
                [
                    {"role": "system", "content": "\n\n".join(blocks)},
                    {"role": "user", "content": "hi"},
                ],
            )

        assert len(posted) == 1
        items = posted[0]
        # 2 block sub-entries + 1 compound + 1 user message = 4
        assert len(items) == 4
        # Block sub-entries have "_block" key
        block_items = [it for it in items if "_block" in it["msg"]]
        assert len(block_items) == 2
        # Compound entry has "_blocks" key
        compound_items = [it for it in items if "_blocks" in it["msg"]]
        assert len(compound_items) == 1
        assert len(compound_items[0]["msg"]["_blocks"]) == 2
        # input_hashes[0] is the compound hash
        block_hashes = [_hash_msg({"_block": b}) for b in blocks]
        compound_hash = _hash_msg({"role": "system", "_blocks": block_hashes})
        assert hashes[0] == compound_hash
        assert hashes[1] == _hash_msg({"role": "user", "content": "hi"})

    def test_changed_block_sends_only_delta(self, cb):
        """Only the changed block + new compound are re-transmitted."""
        block_a = "<notes>\nFirst note.\n</notes>"
        block_b = "<status>\nIdle.\n</status>"
        block_b_updated = "<status>\nBusy.\n</status>"

        posted = []

        def record(url, payload, **kw):
            posted.append(payload)
            return True

        with patch("openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json", side_effect=record):
            from openinference_instrumentation_nemo_oo_agents._context_sideband import set_context_blocks

            # Turn 1: both blocks fresh
            set_context_blocks([block_a, block_b])
            cb._send_new_messages("sess2", [{"role": "system", "content": f"{block_a}\n\n{block_b}"}])
            posted.clear()

            # Turn 2: block_b changed, block_a unchanged
            set_context_blocks([block_a, block_b_updated])
            cb._send_new_messages("sess2", [{"role": "system", "content": f"{block_a}\n\n{block_b_updated}"}])

        assert len(posted) == 1
        items = posted[0]
        # Only the new block sub-entry + new compound are sent; block_a is not re-sent
        block_items = [it for it in items if "_block" in it["msg"]]
        assert len(block_items) == 1
        assert block_items[0]["msg"]["_block"] == block_b_updated
        compound_items = [it for it in items if "_blocks" in it["msg"]]
        assert len(compound_items) == 1

    def test_no_sideband_falls_back_to_whole_message(self, cb):
        """Without ContextVar set, system message is hashed as a whole (old behavior)."""
        from openinference_instrumentation_nemo_oo_agents._context_sideband import _current_blocks

        _current_blocks.set(None)  # explicit clear
        msgs = [{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "hi"}]
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append(payload) or True,
        ):
            hashes = cb._send_new_messages("sess3", msgs)

        assert hashes[0] == _hash_msg(msgs[0])
        items = posted[0]
        assert not any("_blocks" in it.get("msg", {}) for it in items)

    def test_block_sub_entries_sent_before_compound(self, cb):
        """Block sub-entries appear before the compound entry in the POST payload."""
        blocks = ["<a>\nfoo\n</a>", "<b>\nbar\n</b>"]
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append(payload) or True,
        ):
            from openinference_instrumentation_nemo_oo_agents._context_sideband import set_context_blocks

            set_context_blocks(blocks)
            cb._send_new_messages("sess4", [{"role": "system", "content": "\n\n".join(blocks)}])

        items = posted[0]
        positions = {item["h"]: i for i, item in enumerate(items)}
        compound_h = _hash_msg({"role": "system", "_blocks": [_hash_msg({"_block": b}) for b in blocks]})
        block_hs = [_hash_msg({"_block": b}) for b in blocks]
        assert positions[block_hs[0]] < positions[compound_h]
        assert positions[block_hs[1]] < positions[compound_h]

    def test_sideband_cleared_after_read_even_when_first_msg_not_system(self, cb):
        """Sideband is consumed on first system message; subsequent non-system
        first-message calls don't see stale sideband data."""
        from openinference_instrumentation_nemo_oo_agents._context_sideband import (
            get_context_blocks,
            set_context_blocks,
        )

        blocks = ["<a>\nfoo\n</a>"]
        set_context_blocks(blocks)

        # Call with user message first — no system message, sideband not consumed yet
        # (sideband only consumed when first msg is system)
        with patch("openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json", return_value=True):
            cb._send_new_messages("sess5", [{"role": "user", "content": "hi"}])

        # Sideband NOT consumed because first message wasn't system — still set
        # Now simulate an actual system-first call that should consume it
        with patch("openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json", return_value=True):
            cb._send_new_messages("sess5", [{"role": "system", "content": "sys"}])

        # After that call, sideband should be cleared
        assert get_context_blocks() == []

    def test_empty_blocks_list_falls_back_to_whole_message(self, cb):
        """An empty list in the sideband is falsy — should fall back to whole-message hash."""
        from openinference_instrumentation_nemo_oo_agents._context_sideband import set_context_blocks

        set_context_blocks([])  # empty list is falsy
        msgs = [{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "hi"}]
        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append(payload) or True,
        ):
            hashes = cb._send_new_messages("sess6", msgs)

        assert hashes[0] == _hash_msg(msgs[0])
        items = posted[0]
        assert not any("_blocks" in it.get("msg", {}) for it in items)


# ---------------------------------------------------------------------------
# MessageJournalCallback — async hooks
# ---------------------------------------------------------------------------


class TestAsyncJournalCallbackHooks:
    @pytest.fixture
    def cb(self, monkeypatch):
        monkeypatch.setattr(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            lambda url, payload, **kw: True,
        )
        return MessageJournalCallback("http://localhost:5001")

    def test_async_pre_api_call_delegates_to_sync(self, cb):
        """async_log_pre_api_call stores call inputs identically to sync version."""
        msgs = [{"role": "user", "content": "hi"}]
        kwargs = {"litellm_call_id": "async1"}
        with patch.object(cb, "_current_span_id", return_value="spana"):
            asyncio.run(cb.async_log_pre_api_call("gpt-4o", msgs, kwargs))
        assert "async1" in cb._call_inputs
        hashes, span_id = cb._call_inputs["async1"]
        assert len(hashes) == 1
        assert span_id == "spana"

    def test_async_success_event_posts_call_record(self, cb):
        """async_log_success_event posts a call record."""
        import datetime

        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(cb, "_current_span_id", return_value=None):
            asyncio.run(cb.async_log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "async2"}))

        response = MagicMock()
        choice = MagicMock()
        choice.message.model_dump.return_value = {"role": "assistant", "content": "ok"}
        response.choices = [choice]
        response.usage = None

        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
        ):
            asyncio.run(
                cb.async_log_success_event(
                    {"litellm_call_id": "async2", "model": "gpt-4o"},
                    response,
                    datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                    datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.UTC),
                )
            )

        call_posts = [p for url, p in posted if "calls" in url]
        assert len(call_posts) == 1
        assert call_posts[0]["call_id"] == "async2"

    def test_async_failure_event_cleans_up(self, cb):
        """async_log_failure_event removes the pending call from _call_inputs."""
        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(cb, "_current_span_id", return_value=None):
            asyncio.run(cb.async_log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "async3"}))
        assert "async3" in cb._call_inputs
        asyncio.run(cb.async_log_failure_event({"litellm_call_id": "async3"}, None, 0, 0))
        assert "async3" not in cb._call_inputs


# ---------------------------------------------------------------------------
# MessageJournalCallback — span_id capture
# ---------------------------------------------------------------------------


class TestSpanIdCapture:
    def test_span_id_captured_from_active_otel_span(self):
        cb = MessageJournalCallback("http://localhost:5001")
        mock_ctx = MagicMock()
        mock_ctx.is_valid = True
        mock_ctx.span_id = 0x616551E551ACE145
        mock_span = MagicMock()
        mock_span.get_span_context.return_value = mock_ctx

        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal.otel_trace.get_current_span",
            return_value=mock_span,
        ):
            span_id = cb._current_span_id()

        assert span_id == "616551e551ace145"

    def test_span_id_none_when_no_active_span(self):
        cb = MessageJournalCallback("http://localhost:5001")
        mock_ctx = MagicMock()
        mock_ctx.is_valid = False
        mock_span = MagicMock()
        mock_span.get_span_context.return_value = mock_ctx

        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal.otel_trace.get_current_span",
            return_value=mock_span,
        ):
            span_id = cb._current_span_id()

        assert span_id is None

    def test_span_id_none_on_exception(self):
        cb = MessageJournalCallback("http://localhost:5001")
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal.otel_trace.get_current_span",
            side_effect=RuntimeError("no otel"),
        ):
            span_id = cb._current_span_id()
        assert span_id is None


# ---------------------------------------------------------------------------
# MessageJournalCallback — log_pre_api_call / log_success_event
# ---------------------------------------------------------------------------


class TestJournalCallbackHooks:
    @pytest.fixture
    def cb(self, monkeypatch):
        monkeypatch.setattr(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            lambda url, payload, **kw: True,
        )
        cb = MessageJournalCallback("http://localhost:5001")
        return cb

    def test_log_pre_api_call_stores_hashes_and_span_id(self, cb):
        msgs = [{"role": "user", "content": "hi"}]
        kwargs = {"litellm_call_id": "cid1"}
        with patch.object(cb, "_current_span_id", return_value="abc123"):
            cb.log_pre_api_call("gpt-4o", msgs, kwargs)
        hashes, span_id = cb._call_inputs["cid1"]
        assert len(hashes) == 1
        assert span_id == "abc123"

    def test_log_failure_event_cleans_up_call_inputs(self, cb):
        msgs = [{"role": "user", "content": "hi"}]
        cb.log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "cid2"})
        assert "cid2" in cb._call_inputs
        cb.log_failure_event({"litellm_call_id": "cid2"}, None, 0, 0)
        assert "cid2" not in cb._call_inputs

    def test_log_success_event_posts_call_record_with_span_id(self, cb):
        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(cb, "_current_span_id", return_value="span999"):
            cb.log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "cid3"})

        response = MagicMock()
        choice = MagicMock()
        choice.message.model_dump.return_value = {"role": "assistant", "content": "hello"}
        response.choices = [choice]
        response.usage = None

        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
        ):
            import datetime

            cb.log_success_event(
                {"litellm_call_id": "cid3", "model": "gpt-4o"},
                response,
                datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.UTC),
            )

        call_posts = [p for url, p in posted if "calls" in url]
        assert len(call_posts) == 1
        assert call_posts[0]["span_id"] == "span999"
        assert call_posts[0]["model"] == "gpt-4o"
        assert "cid3" not in cb._call_inputs  # cleaned up

    def test_log_success_event_with_cached_tokens(self, cb):
        """tokens dict includes 'cached' key when prompt_tokens_details.cached_tokens > 0."""
        import datetime

        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(cb, "_current_span_id", return_value=None):
            cb.log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "cid_cached"})

        response = MagicMock()
        choice = MagicMock()
        choice.message.model_dump.return_value = {"role": "assistant", "content": "hello"}
        response.choices = [choice]
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 20
        details = MagicMock()
        details.cached_tokens = 80
        usage.prompt_tokens_details = details
        response.usage = usage

        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
        ):
            cb.log_success_event(
                {"litellm_call_id": "cid_cached", "model": "gpt-4o"},
                response,
                datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.UTC),
            )

        call_posts = [p for url, p in posted if "calls" in url]
        assert len(call_posts) == 1
        assert call_posts[0]["tokens"]["cached"] == 80
        assert call_posts[0]["tokens"]["prompt"] == 100
        assert call_posts[0]["tokens"]["completion"] == 20

    def test_log_success_event_no_cached_tokens_key_when_zero(self, cb):
        """tokens dict does NOT include 'cached' key when cached_tokens is 0."""
        import datetime

        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(cb, "_current_span_id", return_value=None):
            cb.log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "cid_nocache"})

        response = MagicMock()
        choice = MagicMock()
        choice.message.model_dump.return_value = {"role": "assistant", "content": "hello"}
        response.choices = [choice]
        usage = MagicMock()
        usage.prompt_tokens = 50
        usage.completion_tokens = 10
        details = MagicMock()
        details.cached_tokens = 0
        usage.prompt_tokens_details = details
        response.usage = usage

        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
        ):
            cb.log_success_event(
                {"litellm_call_id": "cid_nocache", "model": "gpt-4o"},
                response,
                datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.UTC),
            )

        call_posts = [p for url, p in posted if "calls" in url]
        assert len(call_posts) == 1
        assert "cached" not in call_posts[0]["tokens"]

    def test_log_success_event_without_prior_pre_api_call_logs_warning(self, cb):
        """Orphaned log_success_event logs a warning and still posts a call record."""
        import datetime

        response = MagicMock()
        response.choices = []
        response.usage = None

        posted = []
        with (
            patch(
                "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
                side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
            ),
            patch("openinference_instrumentation_nemo_oo_agents._litellm_journal.log") as mock_log,
        ):
            cb.log_success_event(
                {"litellm_call_id": "orphan", "model": "gpt-4o"},
                response,
                datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.UTC),
            )
            mock_log.warning.assert_called_once()

        # Call record still posted but with empty input_hashes
        call_posts = [p for url, p in posted if "calls" in url]
        assert len(call_posts) == 1
        assert call_posts[0]["input_hashes"] == []

    def test_log_success_event_malformed_response_no_choices(self, cb):
        """Malformed response_obj with no .choices attribute does not raise; outputs empty."""
        import datetime

        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(cb, "_current_span_id", return_value=None):
            cb.log_pre_api_call("gpt-4o", msgs, {"litellm_call_id": "cid_bad"})

        response = object()  # no .choices attribute

        posted = []
        with patch(
            "openinference_instrumentation_nemo_oo_agents._litellm_journal._post_json",
            side_effect=lambda url, payload, **kw: posted.append((url, payload)) or True,
        ):
            cb.log_success_event(
                {"litellm_call_id": "cid_bad", "model": "gpt-4o"},
                response,
                datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.UTC),
            )

        call_posts = [p for url, p in posted if "calls" in url]
        assert len(call_posts) == 1
        assert call_posts[0]["output_hashes"] == []


# ---------------------------------------------------------------------------
# exporters.journal() URL parsing
# ---------------------------------------------------------------------------


class TestJournalUrlParsing:
    def setup_method(self):
        try:
            import litellm

            litellm.callbacks = []
        except ImportError:
            pass

    def teardown_method(self):
        try:
            import litellm

            litellm.callbacks = []
        except ImportError:
            pass

    def test_standard_v1_path(self):
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://localhost:5001/v1/traces")
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://localhost:5001"

    def test_non_standard_path_no_v1(self):
        """Endpoints without /v1/ in the path should still extract the origin correctly."""
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://myhost:9000/traces")
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://myhost:9000"

    def test_custom_port_and_path(self):
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://myhost:9000/otel/v1/traces")
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://myhost:9000"

    def test_bare_origin_no_path(self):
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://myhost:9000")
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://myhost:9000"


# ---------------------------------------------------------------------------
# JournalExporter
# ---------------------------------------------------------------------------


class TestJournalExporter:
    def setup_method(self):
        # Clean litellm.callbacks before each test
        try:
            import litellm

            litellm.callbacks = []
        except ImportError:
            pass

    def teardown_method(self):
        try:
            import litellm

            litellm.callbacks = []
        except ImportError:
            pass

    def test_installs_callback_on_construction(self):
        import litellm

        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter
        from openinference_instrumentation_nemo_oo_agents._litellm_journal import MessageJournalCallback

        JournalExporter("http://localhost:5001")
        assert any(isinstance(cb, MessageJournalCallback) for cb in litellm.callbacks)

    def test_idempotent_install(self):
        import litellm

        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter
        from openinference_instrumentation_nemo_oo_agents._litellm_journal import MessageJournalCallback

        JournalExporter("http://localhost:5001")
        JournalExporter("http://localhost:5001")
        count = sum(1 for cb in litellm.callbacks if isinstance(cb, MessageJournalCallback))
        assert count == 1

    def test_shutdown_removes_callback(self):
        import litellm

        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter
        from openinference_instrumentation_nemo_oo_agents._litellm_journal import MessageJournalCallback

        exp = JournalExporter("http://localhost:5001")
        exp.shutdown()
        assert not any(isinstance(cb, MessageJournalCallback) for cb in litellm.callbacks)

    def test_export_is_noop_and_returns_success(self):
        from opentelemetry.sdk.trace.export import SpanExportResult

        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = JournalExporter("http://localhost:5001")
        result = exp.export([])
        assert result == SpanExportResult.SUCCESS

    def test_describe_includes_base_url(self):
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = JournalExporter("http://myhost:9999")
        assert "myhost:9999" in exp.describe()

    def test_force_flush_returns_true(self):
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = JournalExporter("http://localhost:5001")
        assert exp.force_flush() is True


# ---------------------------------------------------------------------------
# exporters.journal() factory
# ---------------------------------------------------------------------------


class TestJournalFactory:
    def setup_method(self):
        try:
            import litellm

            litellm.callbacks = []
        except ImportError:
            pass

    def teardown_method(self):
        try:
            import litellm

            litellm.callbacks = []
        except ImportError:
            pass

    def test_returns_journal_exporter(self):
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://localhost:5001/v1/traces")
        assert isinstance(exp, JournalExporter)

    def test_strips_v1_path(self):
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://localhost:5001/v1/traces")
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://localhost:5001"

    def test_custom_host(self):
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal("http://myhost:9999/v1/traces")
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://myhost:9999"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("OTLP_ENDPOINT", "http://envhost:8888/v1/traces")
        from openinference_instrumentation_nemo_oo_agents import exporters
        from openinference_instrumentation_nemo_oo_agents._journal_exporter import JournalExporter

        exp = exporters.journal()
        assert isinstance(exp, JournalExporter)
        assert exp._base_url == "http://envhost:8888"


# ---------------------------------------------------------------------------
# SpanLimits in enable_tracing
# ---------------------------------------------------------------------------


class TestSpanLimits:
    def test_tracer_provider_has_raised_span_attribute_limit(self):
        """enable_tracing() must create a TracerProvider with max_span_attributes=2048."""
        import openinference_instrumentation_nemo_oo_agents as pkg

        # Reset global state — including OTel's global provider so a TracerProvider
        # set by a prior test doesn't get reused (which would skip SpanLimits init).
        pkg._enabled = False
        pkg._provider = None
        pkg._probe_failed = False

        fake_exporter = MagicMock()
        fake_exporter.__class__.__name__ = "FakeExporter"

        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

        class _FakeExporter(SpanExporter):
            def export(self, spans):
                return SpanExportResult.SUCCESS

        _mock_hooks = MagicMock()

        def _fake_instrument(**kwargs):
            """Simulate what real NemoOOAgentsInstrumentor.instrument() does."""
            from nemo_oo_agents.runtime.hooks import set_hooks

            set_hooks(_mock_hooks)

        with (
            patch("openinference_instrumentation_nemo_oo_agents.NemoOOAgentsInstrumentor") as mock_cls,
            patch("openinference_instrumentation_nemo_oo_agents._instrument_litellm"),
            patch("openinference_instrumentation_nemo_oo_agents.trace.get_tracer_provider", return_value=None),
        ):
            mock_cls.return_value.instrument.side_effect = _fake_instrument
            pkg.enable_tracing(exporters=[_FakeExporter()])

        provider = pkg._provider
        assert isinstance(provider, TracerProvider)
        # SpanLimits are stored on the provider's internal config
        limit = provider._span_limits.max_span_attributes
        assert limit == 2048, f"Expected 2048, got {limit}"

        # Cleanup
        pkg._enabled = False
        pkg._provider = None
        pkg._hooks = None
        from nemo_oo_agents.runtime.hooks import set_hooks

        set_hooks(None)
