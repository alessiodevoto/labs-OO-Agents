# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for trace session name generation and assignment.

Covers:
- ``_make_trace_session_name`` format
- ``bootstrap()`` calls ``set_session`` with the right name after ``_session_id`` is resolved
- ``Session._swap_session_manager`` calls ``set_session`` with the right name
- Both are silent when the tracing package is not installed
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from nemo_oo_agents_cli.tui.session_manager import _make_trace_session_name

# ---------------------------------------------------------------------------
# _make_trace_session_name
# ---------------------------------------------------------------------------

TRACE_NAME_RE = re.compile(r"^tui-\d{8}-\d{6}-[0-9a-f]{8}$")


class TestMakeTraceSessionName:
    def test_format(self):
        """Name must match tui-YYYYMMDD-HHMMSS-<8hex>."""
        name = _make_trace_session_name("abcdef12-0000-0000-0000-000000000000")
        assert TRACE_NAME_RE.match(name), f"Unexpected format: {name!r}"

    def test_uses_session_id_prefix(self):
        """Last 8 chars must be the first 8 chars of the session UUID."""
        sid = "12345678-aaaa-bbbb-cccc-dddddddddddd"
        name = _make_trace_session_name(sid)
        assert name.endswith("-12345678")

    def test_empty_session_id(self):
        """Should not raise on empty string — produces tui-YYYYMMDD-HHMMSS-."""
        name = _make_trace_session_name("")
        assert name.startswith("tui-")


# ---------------------------------------------------------------------------
# Session._swap_session_manager calls set_session
# ---------------------------------------------------------------------------


def _make_mock_session(tmp_path):
    """Build a minimal Session object with mocked dependencies."""
    from nemo_oo_agents_cli.tui.session import Session
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    from nemo_oo_agents.storage import SQLiteStorageManager

    sid = str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    sm = SessionManager(storage=storage, session_id=sid, model="m", agent_cls="A")

    agent = MagicMock()
    agent._storage = storage
    agent.event_manager = MagicMock()
    agent.event_manager.on = MagicMock(return_value=lambda: None)
    agent.event_manager.keys = MagicMock(return_value=[])
    agent.handle = AsyncMock()

    frontend = AsyncMock()
    frontend.render = AsyncMock()
    frontend.get_input = AsyncMock(return_value="")
    frontend.show_python = True
    frontend.clear_streaming_state = MagicMock()

    config = MagicMock()
    config.tui.default_model = "test-model"
    config.tui.vi_mode = False

    registry = MagicMock()
    registry.session_manager = sm
    registry._commands = {}

    session = Session.__new__(Session)
    session.frontend = frontend
    session.agent = agent
    session.config = config
    session.registry = registry
    session._session_manager = sm
    session._background_tasks = set()

    return session, sm, storage


class TestSwapSessionManagerCallsSetSession:
    def test_calls_set_session_with_correct_format(self, tmp_path):
        """_swap_session_manager must call set_session with tui-YYYYMMDD-HHMMSS-<sid8>."""
        import sys

        session, old_sm, _ = _make_mock_session(tmp_path)

        new_sid = str(uuid.uuid4())
        new_sm = MagicMock()
        new_sm.session_id = new_sid
        new_sm._storage = MagicMock()

        captured: list[str] = []
        mock_set_session = MagicMock(side_effect=captured.append)
        fake_tracing = MagicMock()
        fake_tracing.set_session = mock_set_session

        with patch.dict(sys.modules, {"nemo_oo_agents.tracing": fake_tracing}):
            session._swap_session_manager(new_sm)

        assert len(captured) == 1, f"set_session not called; got {captured}"
        assert TRACE_NAME_RE.match(captured[0]), f"Bad format: {captured[0]!r}"
        assert captured[0].endswith(f"-{new_sid[:8]}")

        old_sm.close()

    def test_silent_when_import_fails(self, tmp_path):
        """_swap_session_manager must not raise when tracing is not installed."""
        session, old_sm, _ = _make_mock_session(tmp_path)

        new_sm = MagicMock()
        new_sm.session_id = str(uuid.uuid4())
        new_sm._storage = MagicMock()

        import builtins

        real_import = builtins.__import__

        def _raise_on_tracing(name, *args, **kwargs):
            if "openinference_instrumentation" in name:
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_raise_on_tracing):
            # Should not raise
            session._swap_session_manager(new_sm)

        old_sm.close()

    def test_silent_when_set_session_raises(self, tmp_path):
        """_swap_session_manager must not crash if set_session raises a runtime error."""
        import sys

        session, old_sm, _ = _make_mock_session(tmp_path)

        new_sm = MagicMock()
        new_sm.session_id = str(uuid.uuid4())
        new_sm._storage = MagicMock()

        fake_tracing = MagicMock()
        fake_tracing.set_session = MagicMock(side_effect=RuntimeError("tracing broken"))

        with patch.dict(sys.modules, {"nemo_oo_agents.tracing": fake_tracing}):
            # Should not raise
            session._swap_session_manager(new_sm)

        old_sm.close()
