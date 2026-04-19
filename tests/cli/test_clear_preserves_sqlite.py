# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration-level tests proving ``/clear`` and ``/session new`` don't
destroy the previous session's SQLite data.

**Why this exists.** An earlier bug had ``ClearCommand`` call
``agent.event_manager.clear()``, which wiped the *current* SQLite
storage's events before ``Session._swap_session_manager`` could close
and preserve them. Mock-based tests elsewhere (e.g.
``test_commands.py::test_clear_command_output``) assert
``event_manager.clear`` wasn't called — a negative mock check, not
proof of persistence. These tests use a real ``SQLiteStorageManager``
+ real ``SessionManager`` to prove the positive invariant: old-session
rows stay on disk and reload correctly after the command runs.

Rewritten from the original ``test_clear_bug.py`` after that file was
over-broadly removed during a typeahead-loop cleanup. The integration
path through ``Session.run`` + ``TestFrontend`` isn't restored here —
the unit tests below are what pin the data-preservation invariant;
the full-REPL path is covered indirectly by the other TUI behaviour
tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents_cli.tui.commands import (
    ClearCommand,
    CommandHandler,
    CommandRegistry,
)
from nemo_oo_agents_cli.tui.output import ClearScreen
from nemo_oo_agents_cli.tui.session_manager import SessionManager

# ── helpers ────────────────────────────────────────────────────────────


def _make_sm(tmp_path, *, model="m", agent_cls="A", working_dir="", session_id=None):
    """Create a real SQLite-backed ``SessionManager`` in ``tmp_path``."""
    sid = session_id or str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    return SessionManager(
        storage=storage,
        session_id=sid,
        model=model,
        agent_cls=agent_cls,
        working_dir=working_dir,
    )


def _make_mock_agent(storage: SQLiteStorageManager) -> MagicMock:
    """Mock agent wired to a real ``SQLiteStorageManager``.

    ``event_manager`` delegates to the real backend so ``clear()`` would
    actually destroy data (proves the bug was real) and so survival is
    verifiable (proves the fix works).
    """
    agent = MagicMock()
    agent._storage = storage
    agent.event_manager = storage.event_manager
    agent.respond = AsyncMock()
    # on() must return a callable (unsubscribe fn) for any renderer wiring.
    agent.event_manager.on = MagicMock(return_value=lambda: None)
    return agent


# ── /clear preserves old session's SQLite rows ─────────────────────────


@pytest.mark.asyncio
async def test_clear_does_not_destroy_old_session_data(tmp_path):
    """``ClearCommand`` must leave the old session's events on disk.

    Positive assertion: after ``/clear`` runs and the old session
    manager closes, ``SessionManager.load_turns(old_sid)`` returns the
    turn recorded before the command fired.
    """
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        old_sm = _make_sm(tmp_path)
        old_sm.record_user("remember me")
        old_sid = old_sm.session_id

        agent = _make_mock_agent(old_sm._storage)

        cmd = ClearCommand(
            agent=agent,
            config=MagicMock(default_model="test"),
            frontend=AsyncMock(),
            session_manager=old_sm,
        )

        result = await cmd.execute([])

        assert result.success is True
        assert any(isinstance(o, ClearScreen) for o in result.outputs)

        # Close the newly-created session manager so we can reload the
        # old one's data cleanly.
        if result.new_session_manager is not None:
            result.new_session_manager.close()
        old_sm.close()

        turns = SessionManager.load_turns(old_sid)

    assert len(turns) == 1, (
        f"/clear destroyed the old session's data — expected 1 turn, got {len(turns)}"
    )
    assert turns[0].content == "remember me"


@pytest.mark.asyncio
async def test_clear_resets_in_memory_todos(tmp_path):
    """``/clear`` must wipe the agent's in-memory todo state.

    Swapping storage alone only clears conversation history (events).
    ``TodoManager._todos`` lives on the agent instance, so a naive
    storage swap lets old todos bleed into the new "fresh" session.
    """
    from nemo_oo_agents.tools.todo import TodoManager

    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        old_sm = _make_sm(tmp_path)
        agent = _make_mock_agent(old_sm._storage)
        agent.todo = TodoManager()
        agent.todo.add("task from previous session")
        assert len(agent.todo._todos) == 1

        cmd = ClearCommand(
            agent=agent,
            config=MagicMock(default_model="test"),
            frontend=AsyncMock(),
            session_manager=old_sm,
        )
        result = await cmd.execute([])

        if result.new_session_manager is not None:
            result.new_session_manager.close()
        old_sm.close()

    assert agent.todo._todos == {}, "/clear left stale todos in memory"
    assert agent.todo._order == []


@pytest.mark.asyncio
async def test_clear_without_todo_skill_is_safe(tmp_path):
    """Agents without a ``todo`` attribute still work — the reset helper
    is guarded."""
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        old_sm = _make_sm(tmp_path)
        agent = _make_mock_agent(old_sm._storage)
        if hasattr(agent, "todo"):
            del agent.todo

        cmd = ClearCommand(
            agent=agent,
            config=MagicMock(default_model="test"),
            frontend=AsyncMock(),
            session_manager=old_sm,
        )
        result = await cmd.execute([])
        assert result.success is True

        if result.new_session_manager is not None:
            result.new_session_manager.close()
        old_sm.close()


@pytest.mark.asyncio
async def test_clear_creates_new_session_with_different_id(tmp_path):
    """``/clear`` result carries a fresh ``SessionManager`` distinct
    from the old one — i.e. the caller has something to swap to."""
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        old_sm = _make_sm(tmp_path)
        old_sid = old_sm.session_id

        agent = _make_mock_agent(old_sm._storage)
        cmd = ClearCommand(
            agent=agent,
            config=MagicMock(default_model="test"),
            frontend=AsyncMock(),
            session_manager=old_sm,
        )

        result = await cmd.execute([])

        assert result.new_session_manager is not None
        assert result.new_session_manager.session_id != old_sid

        result.new_session_manager.close()
        old_sm.close()


# ── /session new preserves old session's SQLite rows ───────────────────


@pytest.mark.asyncio
async def test_session_new_does_not_destroy_old_session_data(tmp_path):
    """``/session new`` has the same preservation contract as ``/clear``
    — dispatched via the full ``CommandHandler`` path so any registry
    wiring regressions are caught here too."""
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        old_sm = _make_sm(tmp_path)
        old_sm.record_user("keep this")
        old_sid = old_sm.session_id

        agent = _make_mock_agent(old_sm._storage)

        registry = CommandRegistry(
            frontend=AsyncMock(),
            config=MagicMock(default_model="test"),
            agent=agent,
            session_manager=old_sm,
            skills_dirs=None,
            mcp_file=None,
        )
        handler = CommandHandler(registry=registry, frontend=AsyncMock())

        result = await handler.handle("/session new")

        assert result.success is True

        if result.new_session_manager is not None:
            result.new_session_manager.close()
        old_sm.close()

        turns = SessionManager.load_turns(old_sid)

    assert len(turns) == 1, (
        f"/session new destroyed the old session's data — expected 1 turn, got {len(turns)}"
    )
    assert turns[0].content == "keep this"
