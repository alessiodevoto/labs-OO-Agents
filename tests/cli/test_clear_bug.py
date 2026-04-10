"""Tests proving /clear and /session new must not destroy old session SQLite data.

Before the fix ClearCommand (and SessionCommand "new") called
``agent.event_manager.clear()`` which wiped the *current* SQLite storage's
events before ``Session._swap_session_manager`` could preserve them.

Test structure
--------------
* Unit tests   — exercise the command directly with a real SQLiteStorageManager.
* Integration  — exercise the full REPL loop via ``Session.run()`` +
                 ``TestFrontend`` to prove end-to-end correctness.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents_cli.tui.commands import ClearCommand, CommandHandler, CommandRegistry
from nemo_oo_agents_cli.tui.output import ClearScreen
from nemo_oo_agents_cli.tui.session_manager import SessionManager
from nemo_oo_agents_cli.tui.testing import TestFrontend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sm(tmp_path, *, model="m", agent_cls="A", working_dir="", session_id=None):
    """Create a real SQLite-backed SessionManager in *tmp_path*."""
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
    """Mock agent wired to a real SQLiteStorageManager."""
    agent = MagicMock()
    agent._storage = storage
    # event_manager must delegate to the real backend so that clear() actually
    # destroys data (proving the bug) and that we can verify survival (after fix).
    agent.event_manager = storage.event_manager
    agent.respond = AsyncMock()
    # on() must return a callable (the unsubscribe fn) for _attach_agent.
    agent.event_manager.on = MagicMock(return_value=lambda: None)
    return agent


# ---------------------------------------------------------------------------
# Unit tests — ClearCommand
# ---------------------------------------------------------------------------


class TestClearCommandPreservesHistory:
    @pytest.mark.asyncio
    async def test_clear_does_not_destroy_old_session_data(self, tmp_path):
        """ClearCommand must leave the old session's SQLite events intact.

        Before the fix, ``agent.event_manager.clear()`` wipes the current
        storage before ``_swap_session_manager`` can close and preserve it.
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

            # Close the newly created session manager
            if result.new_session_manager is not None:
                result.new_session_manager.close()
            old_sm.close()

            turns = SessionManager.load_turns(old_sid)

        assert len(turns) == 1, (
            f"/clear destroyed the old session's data — expected 1 turn, got {len(turns)}"
        )
        assert turns[0].content == "remember me"

    @pytest.mark.asyncio
    async def test_clear_creates_new_session(self, tmp_path):
        """/clear result carries a new SessionManager distinct from the old one."""
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


# ---------------------------------------------------------------------------
# Unit tests — SessionCommand /session new
# ---------------------------------------------------------------------------


class TestSessionNewPreservesHistory:
    @pytest.mark.asyncio
    async def test_session_new_does_not_destroy_old_session_data(self, tmp_path):
        """/session new must leave the old session's SQLite events intact."""
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


# ---------------------------------------------------------------------------
# Integration tests — Session.run() + TestFrontend
# ---------------------------------------------------------------------------


class TestTUIViaTestFrontend:
    """Demonstrate that Session.run() is fully exercisable via TestFrontend."""

    def _make_session(self, inputs: list[str], old_sm):
        """Build a Session with a TestFrontend and scripted inputs."""
        from nemo_oo_agents_cli.tui.commands import CommandRegistry
        from nemo_oo_agents_cli.tui.session import Session

        agent = _make_mock_agent(old_sm._storage)

        frontend = TestFrontend(inputs=inputs)

        config = MagicMock()
        config.tui.show_python = False
        config.tui.vi_mode = False
        config.default_model = "test-model"

        registry = CommandRegistry(
            frontend=frontend,
            config=config,
            agent=agent,
            session_manager=old_sm,
            skills_dirs=None,
            mcp_file=None,
        )

        session = Session(
            frontend=frontend,
            agent=agent,
            config=config,
            registry=registry,
            session_manager=old_sm,
        )
        return session, frontend, agent

    @pytest.mark.asyncio
    async def test_repl_handles_exit_command(self, tmp_path):
        """Session.run() terminates cleanly when the user types /exit."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            old_sm = _make_sm(tmp_path)
            session, frontend, _ = self._make_session(["/exit"], old_sm)

            await session.run()

        from nemo_oo_agents_cli.tui.output import TextOutput

        goodbyes = [
            o for o in frontend.outputs if isinstance(o, TextOutput) and "Goodbye" in o.content
        ]
        assert goodbyes, "Expected a Goodbye message after /exit"

    @pytest.mark.asyncio
    async def test_repl_clear_preserves_old_session_via_full_loop(self, tmp_path):
        """Full REPL loop: /clear must not destroy the old session's data."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            old_sm = _make_sm(tmp_path)
            old_sm.record_user("stored in old session")
            old_sid = old_sm.session_id

            session, frontend, _ = self._make_session(["/clear", "/exit"], old_sm)

            await session.run()

            turns = SessionManager.load_turns(old_sid)

        assert len(turns) == 1, (
            f"Full REPL /clear destroyed old session data — expected 1 turn, got {len(turns)}"
        )
        assert turns[0].content == "stored in old session"

    @pytest.mark.asyncio
    async def test_test_frontend_captures_all_outputs(self, tmp_path):
        """TestFrontend.outputs contains every Output rendered during the session."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            old_sm = _make_sm(tmp_path)
            session, frontend, _ = self._make_session(["/help", "/exit"], old_sm)

            await session.run()

        from nemo_oo_agents_cli.tui.output import HelpOutput

        assert frontend.outputs_of(HelpOutput), "Expected a HelpOutput from /help"
