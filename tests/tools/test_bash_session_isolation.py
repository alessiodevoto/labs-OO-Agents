"""Tests for BashTool session isolation and FileTool rg fallback.

These tests verify:
1. BashTool.run() uses start_new_session=True (subprocess can't access /dev/tty)
2. BashTool.run() kills the entire process group on timeout (no orphans)
3. FileTool.find/grep fall back gracefully when rg is unavailable
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from nooa.tools.bash_tool import BashTool, FileTool

# ============================================================================
# Session isolation: subprocess cannot access /dev/tty
# ============================================================================


@pytest.mark.asyncio
async def test_subprocess_cannot_access_dev_tty():
    """Subprocesses spawned by BashTool must not be able to open /dev/tty.

    start_new_session=True creates a new session with no controlling terminal,
    so open("/dev/tty") fails with ENXIO. This prevents git, ssh, gpg, etc.
    from stealing the TUI's terminal.
    """
    tool = BashTool()
    # Use O_RDWR|O_NONBLOCK to avoid blocking if /dev/tty is available.
    # With start_new_session=True, the open() itself should fail with ENXIO.
    # Without it, the open succeeds and prints "GOT TTY".
    result = await tool.run(
        "python3 -c \"import os; fd = os.open('/dev/tty', os.O_RDWR | os.O_NONBLOCK); os.close(fd); print('GOT TTY')\"",
        timeout=5,
    )
    assert "GOT TTY" not in result.stdout, (
        "Subprocess could open /dev/tty — start_new_session=True is not set"
    )
    assert result.return_code != 0


@pytest.mark.asyncio
async def test_subprocess_runs_in_new_session():
    """Subprocesses spawned by BashTool must have a different session ID from the parent.

    start_new_session=True calls setsid(), giving the child a new SID.
    """
    tool = BashTool()
    parent_sid = os.getsid(0)
    result = await tool.run(
        'python3 -c "import os; print(os.getsid(0))"',
        timeout=5,
    )
    assert result.success is True
    child_sid = int(result.stdout.strip())
    assert child_sid != parent_sid, (
        f"Child SID {child_sid} == parent SID {parent_sid}; "
        "start_new_session=True is not being passed"
    )


# ============================================================================
# Timeout kills entire process group (no orphans)
# ============================================================================


@pytest.mark.asyncio
async def test_timeout_kills_process_group():
    """On timeout, BashTool must kill the entire process group, not just the shell.

    Spawns a command that starts a background child which writes its PID to a file.
    After the BashTool timeout fires, checks whether that child PID is still alive.
    Without killpg, the child survives as an orphan.
    """
    tool = BashTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = os.path.join(tmpdir, "child.pid")

        # Shell spawns a background sleep, writes its PID, then waits.
        # BashTool timeout kills the shell. If killpg works, the sleep dies too.
        result = await tool.run(
            f"bash -c 'sleep 600 & echo $! > {pidfile}; wait'",
            timeout=1,
        )
        assert result.return_code == -1
        assert "timed out" in result.stderr.lower()

        # Give the OS a moment to reap
        await asyncio.sleep(0.3)

        # Read the child PID and check if it's still alive
        if os.path.exists(pidfile):
            child_pid = int(Path(pidfile).read_text().strip())
            try:
                os.kill(child_pid, 0)  # signal 0 = check if alive
                # Process is still alive — orphan! Clean up, then fail.
                os.kill(child_pid, 9)
                pytest.fail(
                    f"Child process {child_pid} survived timeout — "
                    "killpg is not being used to kill the process group"
                )
            except ProcessLookupError:
                pass  # Process is dead — killpg worked correctly
            except PermissionError:
                pass  # Can't check, skip
        # If pidfile doesn't exist, shell was killed before writing — also OK.


# ============================================================================
# FileTool.find() — rg with fallback to find(1)
# ============================================================================


@pytest.mark.asyncio
async def test_find_uses_rg_when_available():
    """FileTool.find() uses rg --files when rg is available."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "foo.py").write_text("pass")
        Path(tmpdir, "bar.txt").write_text("hello")

        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)
        result = await ft.find("*.py", tmpdir)
        assert result.success is True
        assert "foo.py" in result.stdout
        assert "bar.txt" not in result.stdout


@pytest.mark.asyncio
async def test_find_falls_back_when_rg_unavailable():
    """FileTool.find() falls back to find(1) when rg is not installed.

    Patches _rg_available to simulate rg missing.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "foo.py").write_text("pass")
        Path(tmpdir, "bar.txt").write_text("hello")

        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)

        with patch.object(ft, "_rg_available", False):
            result = await ft.find("*.py", tmpdir)
            assert "foo.py" in result.stdout
            assert "bar.txt" not in result.stdout


@pytest.mark.asyncio
async def test_find_rg_exit1_no_match_is_not_error():
    """FileTool.find() returns empty stdout and rc=0 when no files match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)
        result = await ft.find("*.xyz", tmpdir)
        assert result.return_code == 0
        assert result.stdout.strip() == ""


# ============================================================================
# FileTool.grep() — rg with fallback to grep(1)
# ============================================================================


@pytest.mark.asyncio
async def test_grep_uses_rg_when_available():
    """FileTool.grep() uses rg when available."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "test.py").write_text("def main():\n    pass\n")

        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)
        result = await ft.grep("def main", f"{tmpdir}/test.py")
        assert "def main" in result.stdout


@pytest.mark.asyncio
async def test_grep_falls_back_when_rg_unavailable():
    """FileTool.grep() falls back to grep -n for single files when rg is unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "test.py").write_text("def main():\n    pass\n")

        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)

        with patch.object(ft, "_rg_available", False):
            result = await ft.grep("def main", f"{tmpdir}/test.py")
            assert "def main" in result.stdout


@pytest.mark.asyncio
async def test_grep_directory_falls_back_when_rg_unavailable():
    """FileTool.grep() falls back to grep -rn for directories when rg is unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.py").write_text("hello world\n")
        Path(tmpdir, "b.py").write_text("goodbye\n")

        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)

        with patch.object(ft, "_rg_available", False):
            result = await ft.grep("hello", tmpdir)
            assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_grep_no_match_is_not_error():
    """FileTool.grep() returns empty stdout and rc=0 when nothing matches."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "test.py").write_text("hello\n")
        tool = BashTool(working_dir=tmpdir)
        ft = FileTool(tool)
        result = await ft.grep("NONEXISTENT_PATTERN_XYZ", f"{tmpdir}/test.py")
        assert result.return_code == 0
        assert result.stdout.strip() == ""
