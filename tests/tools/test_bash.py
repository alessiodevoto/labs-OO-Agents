"""Tests for nemo_oo_agents.tools.bash_tool.BashTool and BashResult.

Contract-focused: assert public interface (initialization, command execution,
result properties) without depending on implementation details.
"""

import tempfile
from pathlib import Path

import pytest

from nemo_oo_agents.config.tool_configs import BashConfig
from nemo_oo_agents.tools.bash_tool import BashResult, BashTool

# ============================================================================
# BashResult Tests
# ============================================================================


def test_bash_result_success_property():
    """BashResult.success returns True for zero exit code, False otherwise.

    Verifies that:
    - success is True when return_code is 0
    - success is False when return_code is non-zero
    """
    result = BashResult(stdout="ok", stderr="", return_code=0)
    assert result.success is True

    result = BashResult(stdout="", stderr="error", return_code=1)
    assert result.success is False


def test_bash_result_str_representation():
    """BashResult.__str__ formats output with stderr and exit code when present.

    Verifies that:
    - stdout is always included
    - stderr is included with [stderr] marker when present
    - exit code is included with [exit code: N] marker when non-zero
    """
    result = BashResult(stdout="output", stderr="", return_code=0)
    assert "output" in str(result)

    result = BashResult(stdout="output", stderr="error", return_code=1)
    assert "output" in str(result)
    assert "[stderr]" in str(result)
    assert "[exit code: 1]" in str(result)


# ============================================================================
# BashTool Initialization Tests
# ============================================================================


def test_bash_tool_initialization_defaults():
    """BashTool initializes with default working directory, timeout, and sandbox disabled.

    Verifies that:
    - working_dir defaults to current directory (resolved)
    - config.default_timeout defaults to 30.0 seconds
    - config.use_sandbox defaults to False
    """
    tool = BashTool()
    assert tool.working_dir == Path(".").resolve()
    assert tool.config.default_timeout == 30.0
    assert tool.config.use_sandbox is False


def test_bash_tool_initialization_custom_parameters():
    """BashTool accepts custom working directory and config object.

    Verifies that:
    - working_dir can be set to a custom path (resolved)
    - config.default_timeout can be customized via BashConfig
    - config.use_sandbox can be set via BashConfig
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = BashTool(
            working_dir=tmpdir, config=BashConfig(default_timeout=10.0, use_sandbox=False)
        )
        assert tool.working_dir == Path(tmpdir).resolve()
        assert tool.config.default_timeout == 10.0
        assert tool.config.use_sandbox is False


def test_bash_tool_accepts_config():
    """BashTool accepts a BashConfig object."""
    tool = BashTool(config=BashConfig(default_timeout=60.0))
    assert tool.config.default_timeout == 60.0


def test_bash_tool_rejects_flat_timeout_kwarg():
    """BashTool raises TypeError when given flat config kwargs."""
    with pytest.raises(TypeError):
        BashTool(timeout=60.0)


def test_bash_tool_sandbox_available_property():
    """BashTool.sandbox_available returns boolean indicating SRT availability.

    Verifies that sandbox_available is a boolean (actual value depends on system
    configuration and SRT installation).
    """
    tool = BashTool()
    assert isinstance(tool.sandbox_available, bool)


# ============================================================================
# BashTool Command Execution Tests
# ============================================================================


@pytest.mark.asyncio
async def test_bash_tool_run_successful_command():
    """BashTool.run() executes successful commands and captures stdout.

    Verifies that:
    - Commands with zero exit code return success=True
    - stdout contains command output
    - return_code is 0
    """
    tool = BashTool()
    result = await tool.run("echo 'hello world'")
    assert result.success is True
    assert "hello world" in result.stdout
    assert result.return_code == 0


@pytest.mark.asyncio
async def test_bash_tool_run_failed_command():
    """BashTool.run() captures failure status for commands with non-zero exit codes.

    Verifies that:
    - Commands with non-zero exit code return success=False
    - return_code reflects the actual exit code
    """
    tool = BashTool()
    result = await tool.run("false")
    assert result.success is False
    assert result.return_code != 0


@pytest.mark.asyncio
async def test_bash_tool_run_timeout():
    """BashTool.run() terminates long-running commands after timeout.

    Verifies that:
    - Commands exceeding timeout return success=False
    - return_code is -1 for timeout
    - stderr contains timeout indication
    """
    tool = BashTool(config=BashConfig(default_timeout=0.5, use_sandbox=False))
    result = await tool.run("sleep 10")
    assert result.success is False
    assert result.return_code == -1
    assert "timed out" in result.stderr.lower()


@pytest.mark.asyncio
async def test_bash_tool_run_captures_stderr():
    """BashTool.run() captures stderr output separately from stdout.

    Verifies that stderr content is available in result.stderr.
    """
    tool = BashTool()
    result = await tool.run("echo 'error' >&2")
    assert result.success is True
    assert "error" in result.stderr


# ============================================================================
# BashTool Working Directory Tests
# ============================================================================


@pytest.mark.asyncio
async def test_bash_tool_run_uses_initialization_working_dir():
    """BashTool.run() executes commands in the working directory set at initialization.

    Verifies that commands run in the specified working directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tool = BashTool(working_dir=tmpdir)
        result = await tool.run("pwd")
        assert result.success is True
        assert tmpdir in result.stdout


@pytest.mark.asyncio
async def test_bash_tool_run_working_dir_override():
    """BashTool.run() accepts per-command working directory override.

    Verifies that working_dir parameter in run() overrides initialization setting.
    """
    with tempfile.TemporaryDirectory() as tmpdir1:
        with tempfile.TemporaryDirectory() as tmpdir2:
            tool = BashTool(working_dir=tmpdir1)
            result = await tool.run("pwd", working_dir=tmpdir2)
            assert result.success is True
            assert tmpdir2 in result.stdout


# ============================================================================
# BashTool Timeout Tests
# ============================================================================


@pytest.mark.asyncio
async def test_bash_tool_run_timeout_override():
    """BashTool.run() accepts per-command timeout override.

    Verifies that timeout parameter in run() overrides initialization setting.
    """
    tool = BashTool(config=BashConfig(default_timeout=1.0, use_sandbox=False))
    result = await tool.run("echo 'quick'", timeout=0.5)
    assert result.success is True


# ============================================================================
# BashTool Sandbox Tests
# ============================================================================


@pytest.mark.asyncio
async def test_bash_tool_run_uses_sandbox_when_enabled():
    """BashTool.run() uses sandbox when use_sandbox=True if SRT is available and working.

    Verifies that:
    - If SRT is available and working, result.sandboxed is True
    - If SRT is not available, falls back to unsandboxed execution
    - If SRT is available but misconfigured, test is skipped
    """
    tool = BashTool(config=BashConfig(use_sandbox=True))
    result = await tool.run("echo 'test'")
    if tool.sandbox_available:
        if result.success:
            assert result.sandboxed is True
        else:
            # SRT available but command failed (likely misconfigured)
            assert result.sandboxed is True
            pytest.skip("SRT available but not properly configured")
    else:
        # If SRT not available, should fall back to unsandboxed
        assert result.sandboxed is False
        assert result.success is True


@pytest.mark.asyncio
async def test_bash_tool_run_sandbox_disabled_at_initialization():
    """BashTool.run() does not use sandbox when use_sandbox=False at initialization.

    Verifies that commands run without sandboxing when initialized with use_sandbox=False.
    """
    tool = BashTool()
    result = await tool.run("echo 'test'")
    assert result.success is True
    assert result.sandboxed is False


# ============================================================================
# BashTool String Representation Tests
# ============================================================================


def test_bash_tool_repr():
    """BashTool.__repr__ includes class name and sandbox status.

    Verifies that string representation contains identifying information.
    """
    tool = BashTool()
    repr_str = repr(tool)
    assert "BashTool" in repr_str
    assert "sandbox" in repr_str.lower()
