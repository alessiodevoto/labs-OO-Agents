# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Thread-safety tests for SessionManager (gl-212).

The writer thread has been replaced by a threading.RLock on
SQLiteEventBackend — writes now execute inline on the caller's thread
and the lock serializes concurrent access from multiple threads.
"""

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


class TestInlineWrites:
    """Verify writes are persisted correctly with lock-based serialization."""

    def test_record_user_persisted(self, tmp_path):
        """record_user persists the event immediately."""
        sm = _make_sm(tmp_path)
        try:
            sm.record_user("hello")
            sm.close()

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
        """rename persists the event immediately."""
        sm = _make_sm(tmp_path)
        try:
            sm.rename("new name", user_named=True)
            assert sm.name == "new name"
            assert sm.user_named is True
            sm.close()

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

    def test_writes_execute_on_caller_thread(self, tmp_path):
        """Writes execute inline on the caller's thread (no writer thread)."""
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

            assert len(write_thread_ids) == 1
            assert write_thread_ids[0] == caller_thread_id, (
                "Write should run inline on the caller thread"
            )
        finally:
            sm.close()

    def test_concurrent_writes_from_multiple_threads(self, tmp_path):
        """Multiple threads can safely write concurrently (RLock serializes)."""
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
            sm.close()
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

    def test_close_is_idempotent(self, tmp_path):
        """Multiple close() calls don't raise."""
        sm = _make_sm(tmp_path)
        sm.record_user("test")
        sm.close()
        sm.close()  # should not raise

    def test_many_rapid_writes(self, tmp_path):
        """Rapid burst of writes — all persist immediately."""
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

    def test_threaded_flag_is_always_false(self, tmp_path):
        """The _threaded flag is always False (writer thread removed)."""
        sm = _make_sm(tmp_path)
        try:
            assert not sm._threaded
        finally:
            sm.close()

        # Also verify with check_same_thread=True
        sid = str(uuid.uuid4())
        storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
        sm2 = SessionManager(
            storage=storage, session_id=sid, model="test", agent_cls="T", working_dir=""
        )
        try:
            assert not sm2._threaded
            sm2.record_user("inline write")
            events = list(sm2._event_manager._backend.all_events())
            user_events = [e for e in events if getattr(e, "event_type", "") == "TUIUserInput"]
            assert len(user_events) == 1
        finally:
            sm2.close()

    def test_concurrent_read_write_from_agent_and_session(self, tmp_path):
        """Simulate the real TUI pattern: agent thread reads while session writes.

        This is the core gl-212 race condition that the RLock fixes.
        """
        sid = str(uuid.uuid4())
        storage = SQLiteStorageManager(tmp_path / f"{sid}.db", check_same_thread=False)
        backend = storage.event_backend

        # Pre-populate with events (simulates an active session)
        from nemo_oo_agents.context_blocks import Metadata

        for i in range(20):
            backend.store(f"evt-{i}", Metadata(content=f"event {i}"))

        sm = SessionManager(
            storage=storage, session_id=sid, model="test", agent_cls="T", working_dir=""
        )

        errors = []
        barrier = threading.Barrier(3)

        def _agent_reader():
            """Simulate agent reading events (build_context)."""
            try:
                barrier.wait(timeout=5)
                for _ in range(100):
                    tags = backend.active_tags()
                    for tag in tags[:5]:
                        backend.get(tag)
            except Exception as e:
                errors.append(("reader", e))

        def _session_writer():
            """Simulate session recording user input."""
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    sm.record_user(f"msg-{i}")
            except Exception as e:
                errors.append(("writer", e))

        reader = threading.Thread(target=_agent_reader)
        writer = threading.Thread(target=_session_writer)
        reader.start()
        writer.start()
        barrier.wait(timeout=5)
        reader.join(timeout=30)
        writer.join(timeout=30)

        try:
            assert not errors, f"Thread-safety violation: {errors}"
            assert not reader.is_alive()
            assert not writer.is_alive()
        finally:
            sm.close()
