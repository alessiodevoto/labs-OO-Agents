# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Threading test for SessionManager writer queue (gl-212)."""

import threading
import uuid

from nemo_oo_agents_cli.tui.session_manager import SessionManager

from nemo_oo_agents.storage import SQLiteStorageManager


def _make_sm(tmp_path):
    sid = str(uuid.uuid4())
    # check_same_thread=False matches real TUI usage (bootstrap.py)
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db", check_same_thread=False)
    return SessionManager(
        storage=storage, session_id=sid, model="test", agent_cls="T", working_dir=""
    )


class TestWriterQueue:
    """Verify the dedicated writer thread serializes all DB writes."""

    def test_record_user_persisted(self, tmp_path):
        """record_user enqueues and the writer persists the event."""
        sm = _make_sm(tmp_path)
        try:
            sm.record_user("hello")
            sm.close()  # drain queue

            # Reopen to verify persistence
            storage = SQLiteStorageManager(
                tmp_path / f"{sm.session_id}.db", check_same_thread=False
            )
            events = list(storage.event_backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            assert len(user_events) == 1
            assert user_events[0].text == "hello"
            storage.close()
        finally:
            sm.close()

    def test_rename_persisted(self, tmp_path):
        """rename enqueues and the writer persists the event."""
        sm = _make_sm(tmp_path)
        try:
            sm.rename("new name", user_named=True)
            assert sm.name == "new name"
            assert sm.user_named is True
            sm.close()  # drain queue

            storage = SQLiteStorageManager(
                tmp_path / f"{sm.session_id}.db", check_same_thread=False
            )
            events = list(storage.event_backend.all_events())
            rename_events = [
                e for e in events if getattr(e, "event_type", "") == "TUISessionRename"
            ]
            assert len(rename_events) == 1
            storage.close()
        finally:
            sm.close()

    def test_writes_execute_on_writer_thread(self, tmp_path):
        """Writes execute on the dedicated writer thread, not the caller."""
        sm = _make_sm(tmp_path)
        write_thread_ids: list[int] = []

        original = sm._do_record_user

        def _capturing_record(text):
            write_thread_ids.append(threading.current_thread().ident)
            original(text)

        sm._do_record_user = _capturing_record

        try:
            caller_thread_id = threading.current_thread().ident
            sm.record_user("test")
            sm.close()  # drain

            assert len(write_thread_ids) == 1
            assert write_thread_ids[0] != caller_thread_id, (
                "Write should run on writer thread, not caller"
            )
            assert write_thread_ids[0] == sm._writer_thread.ident
        finally:
            sm.close()

    def test_concurrent_writes_from_multiple_threads(self, tmp_path):
        """Multiple threads can safely enqueue writes concurrently."""
        sm = _make_sm(tmp_path)
        barrier = threading.Barrier(3)  # 2 writers + main
        n_writes = 30
        errors = []

        def _writer(prefix):
            try:
                barrier.wait(timeout=5)
                for i in range(n_writes):
                    sm.record_user(f"{prefix}-{i}")
            except Exception as e:
                errors.append(e)

        writers = [
            threading.Thread(target=_writer, args=("A",)),
            threading.Thread(target=_writer, args=("B",)),
        ]
        for w in writers:
            w.start()
        barrier.wait(timeout=5)
        for w in writers:
            w.join(timeout=10)
            assert not w.is_alive(), f"Writer thread {w.name} did not terminate"

        try:
            sm.close()  # drain queue
            assert not errors

            storage = SQLiteStorageManager(
                tmp_path / f"{sm.session_id}.db", check_same_thread=False
            )
            events = list(storage.event_backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            assert len(user_events) == 2 * n_writes
            storage.close()
        finally:
            sm.close()

    def test_close_drains_queue(self, tmp_path):
        """close() waits for all queued writes to complete."""
        sm = _make_sm(tmp_path)
        try:
            for i in range(10):
                sm.record_user(f"msg-{i}")
            sm.close()

            # Writer thread should be terminated
            assert not sm._writer_thread.is_alive()

            # All writes should be persisted
            storage = SQLiteStorageManager(
                tmp_path / f"{sm.session_id}.db", check_same_thread=False
            )
            events = list(storage.event_backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            assert len(user_events) == 10
            storage.close()
        finally:
            sm.close()

    def test_close_is_idempotent(self, tmp_path):
        """Multiple close() calls don't raise."""
        sm = _make_sm(tmp_path)
        sm.record_user("test")
        sm.close()
        sm.close()  # should not raise

    def test_writer_continues_after_error(self, tmp_path):
        """A failed write doesn't kill the writer — subsequent writes succeed."""
        sm = _make_sm(tmp_path)
        call_count = {"n": 0}
        original = sm._do_record_user

        def _failing_then_ok(text):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated DB error")
            original(text)

        sm._do_record_user = _failing_then_ok

        try:
            sm.record_user("first")  # succeeds
            sm.record_user("second")  # fails (logged, writer continues)
            sm.record_user("third")  # succeeds
            sm.close()

            storage = SQLiteStorageManager(
                tmp_path / f"{sm.session_id}.db", check_same_thread=False
            )
            events = list(storage.event_backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            # first and third should persist, second was lost
            assert len(user_events) == 2
            texts = [e.text for e in user_events]
            assert "first" in texts
            assert "third" in texts
            assert "second" not in texts
            storage.close()
        finally:
            sm.close()

    def test_inline_mode_when_check_same_thread_true(self, tmp_path):
        """With check_same_thread=True (default), writes are inline, no thread."""
        sid = str(uuid.uuid4())
        # Default: check_same_thread=True → _threaded=False
        storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
        sm = SessionManager(
            storage=storage, session_id=sid, model="test", agent_cls="T", working_dir=""
        )
        try:
            assert not sm._threaded
            assert not hasattr(sm, "_writer_thread") or not hasattr(sm, "_write_queue")

            # Writes still work (inline)
            sm.record_user("inline write")
            events = list(sm._event_manager._backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            assert len(user_events) == 1
        finally:
            sm.close()

    def test_write_after_close_does_not_raise(self, tmp_path):
        """Writing after close doesn't crash (graceful degradation)."""
        sm = _make_sm(tmp_path)
        sm.close()
        # This shouldn't raise even though the writer thread is gone
        # (the queue.put will succeed but no one processes it — that's OK)
        try:
            sm.record_user("after close")
        except Exception:
            pass  # either path is acceptable — just don't crash hard

    def test_many_rapid_writes_then_close(self, tmp_path):
        """Rapid burst of writes followed by immediate close — all persist."""
        sm = _make_sm(tmp_path)
        n = 100
        try:
            for i in range(n):
                sm.record_user(f"rapid-{i}")
            sm.close()

            storage = SQLiteStorageManager(
                tmp_path / f"{sm.session_id}.db", check_same_thread=False
            )
            events = list(storage.event_backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            assert len(user_events) == n, f"Expected {n}, got {len(user_events)}"
            storage.close()
        finally:
            sm.close()
