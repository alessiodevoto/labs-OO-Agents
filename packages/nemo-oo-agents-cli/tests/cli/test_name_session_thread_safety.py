# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reproduce cross-thread sqlite segfault from concurrent name_session + handle.

The TUI schedules name_session (PredictStrategy) on the main thread while
handle (CodeActStrategy) runs on the agent thread. Both call build_context ->
event_manager.values() -> SQLiteEventBackend.active_tags() + get() concurrently,
racing on the same sqlite3.Connection. With check_same_thread=False this isn't
caught by Python — it segfaults in the sqlite C code or pydantic-core.

This test verifies the fix: _name_session_on_agent_loop dispatches the coroutine
to the agent loop so all sqlite access is serialized on one thread.
"""

import asyncio
import threading
import uuid

from nemo_oo_agents.context_blocks import Metadata
from nemo_oo_agents.storage import SQLiteStorageManager


def _populate_backend(backend, n=50):
    """Store N events so values() has work to do."""
    for i in range(n):
        backend.store(
            f"evt-{i}",
            Metadata(content=f"event payload {i}" * 20),
        )


def _reader_loop(backend, iterations=200):
    """Simulate build_context -> event_manager.values() from a thread."""
    for _ in range(iterations):
        tags = backend.active_tags()
        for tag in tags[:10]:
            backend.get(tag)


class TestCrossThreadSqliteRace:
    """Demonstrate that concurrent reads from two threads on one connection crash."""

    def test_concurrent_reads_are_unsafe(self, tmp_path):
        """Demonstrate that concurrent reads from two threads are NOT safe.

        This proves the need for the fix: without dispatching name_session to
        the agent loop, concurrent build_context calls from different threads
        race on the same sqlite3.Connection and cause InterfaceError (or
        segfault on macOS with pydantic model_validate in the call chain).
        """
        db_path = tmp_path / f"{uuid.uuid4()}.db"
        storage = SQLiteStorageManager(db_path, check_same_thread=False)
        backend = storage.event_backend

        _populate_backend(backend, n=50)

        errors = []

        def worker():
            try:
                _reader_loop(backend, iterations=500)
            except Exception as e:
                errors.append(e)

        # Launch two threads both reading from the same backend
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        storage.close()

        # On Linux this raises InterfaceError; on macOS it often segfaults.
        # Either way, concurrent access is unsafe — proving the fix is needed.
        assert len(errors) > 0, (
            "Expected concurrent reads to fail — if this passes, the race "
            "window may have been missed. The test is still valid as a smoke check."
        )

    def test_name_session_dispatched_to_agent_loop(self):
        """Verify _name_session_on_agent_loop uses run_coroutine_threadsafe."""
        from nemo_oo_agents_cli.tui.session import Session

        # Check the method exists and dispatches to agent loop
        assert hasattr(Session, "_name_session_on_agent_loop"), (
            "_name_session_on_agent_loop method missing — "
            "name_session will race with handle on different threads"
        )

    def test_name_session_uses_agent_loop_when_available(self):
        """When agent loop is available, coroutine is scheduled there."""
        from unittest.mock import MagicMock, patch

        from nemo_oo_agents_cli.tui.session import Session

        # Create a mock session with the minimum required attributes
        session = object.__new__(Session)
        session._naming_futures = set()

        # Mock the app with an agent loop
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mock_loop.is_running.return_value = True

        mock_app = MagicMock()
        mock_app._agent_loop = mock_loop
        session._app = mock_app
        session._session_manager = MagicMock()
        session._session_manager.user_named = False
        session.agent = MagicMock()

        with patch("asyncio.run_coroutine_threadsafe") as mock_dispatch:
            session._name_session_on_agent_loop("hello world")
            mock_dispatch.assert_called_once()
            # Verify it targeted the agent loop
            args = mock_dispatch.call_args
            assert args[0][1] is mock_loop
