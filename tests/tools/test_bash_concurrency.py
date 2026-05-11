# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for new BashSession and ShellTools APIs added in the concurrency fix."""

import asyncio

import pytest

from nemo_oo_agents.tools._bash_session import BashSession
from nemo_oo_agents.tools.shell_tools import ShellTools


class TestRunWithTimeoutFlag:
    """Tests for BashSession.run_with_timeout_flag()."""

    @pytest.fixture
    async def session(self, tmp_path):
        session = BashSession(cwd=tmp_path)
        await session.start()
        yield session
        await session.close()

    async def test_returns_four_tuple_on_success(self, session):
        stdout, stderr, code, timed_out = await session.run_with_timeout_flag("echo hello")
        assert stdout == "hello"
        assert code == 0
        assert timed_out is False

    async def test_returns_timed_out_true_on_timeout(self, session):
        stdout, stderr, code, timed_out = await session.run_with_timeout_flag(
            "sleep 10", timeout=0.5
        )
        assert code == 124
        assert timed_out is True

    async def test_exit_code_124_not_confused_with_timeout(self, session):
        """A command that exits 124 naturally should NOT report timed_out=True."""
        stdout, stderr, code, timed_out = await session.run_with_timeout_flag(
            "bash -c 'exit 124'", timeout=5
        )
        assert code == 124
        assert timed_out is False


class TestAsyncContextManager:
    """Tests for BashSession async with protocol."""

    async def test_async_with_starts_and_closes(self, tmp_path):
        async with BashSession(cwd=tmp_path) as session:
            stdout, stderr, code = await session.run("echo works")
            assert stdout == "works"
            assert code == 0
        # After exit, session should be closed
        assert session._started is False
        assert session._process is None

    async def test_async_with_closes_on_exception(self, tmp_path):
        with pytest.raises(ValueError):
            async with BashSession(cwd=tmp_path) as session:
                raise ValueError("test")
        assert session._started is False


class TestLockSerialization:
    """Tests that concurrent run() calls are serialized."""

    async def test_concurrent_runs_dont_corrupt(self, tmp_path):
        """Two concurrent run() calls should both succeed without corruption."""
        session = BashSession(cwd=tmp_path)
        await session.start()
        try:
            results = await asyncio.gather(
                session.run("echo first"),
                session.run("echo second"),
            )
            outputs = {r[0] for r in results}
            assert "first" in outputs
            assert "second" in outputs
            # Both should have exit code 0
            assert all(r[2] == 0 for r in results)
        finally:
            await session.close()


class TestCwdGuard:
    """Tests for ShellTools.cwd_guard()."""

    async def test_cwd_restored_after_cd(self, tmp_path):
        shell = ShellTools(cwd=tmp_path)
        try:
            original_cwd = shell._session.cwd

            async with shell.cwd_guard():
                await shell.run("cd /tmp")
                assert shell._session.cwd != original_cwd

            # After the guard, cwd should be restored
            assert shell._session.cwd == original_cwd
        finally:
            await shell.close()

    async def test_cwd_restored_on_exception(self, tmp_path):
        shell = ShellTools(cwd=tmp_path)
        try:
            original_cwd = shell._session.cwd

            with pytest.raises(RuntimeError):
                async with shell.cwd_guard():
                    await shell.run("cd /tmp")
                    raise RuntimeError("oops")

            assert shell._session.cwd == original_cwd
        finally:
            await shell.close()

    async def test_file_cursor_restored(self, tmp_path):
        shell = ShellTools(cwd=tmp_path)
        try:
            # Create a test file
            (tmp_path / "test.py").write_text("line1\nline2\n")
            await shell.view("test.py")
            assert shell._current_file == "test.py"

            async with shell.cwd_guard():
                shell._current_file = "other.py"
                shell._current_line = 99

            assert shell._current_file == "test.py"
        finally:
            await shell.close()
