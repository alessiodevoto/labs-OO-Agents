# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test that BashTool timeout actually kills commands."""

import time

import pytest

from nemo_oo_agents.tools.bash_tool import BashTool


@pytest.mark.asyncio
async def test_bash_timeout_returns_within_deadline():
    """BashTool.run() with a 2s timeout must return within ~3s, not hang."""
    bash = BashTool()
    start = time.monotonic()
    result = await bash.run("sleep 30", timeout=2)
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"BashTool.run() took {elapsed:.1f}s with timeout=2"
    assert result.return_code == -1
    assert "timed out" in result.stderr


@pytest.mark.asyncio
async def test_bash_timeout_kills_pipeline():
    """Timeout must kill all processes in a shell pipeline, not just the parent.

    A pipeline like `slow_cmd | wc -l` spawns a process group. If we only
    kill the top-level shell, child processes can linger and the await on
    proc.communicate() may never complete (the pipe stays open).
    """
    bash = BashTool()
    start = time.monotonic()
    # This pipeline will hang: find searches everything, pipe keeps it open
    result = await bash.run("sleep 30 | cat", timeout=2)
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"Pipeline took {elapsed:.1f}s with timeout=2"
    assert result.return_code == -1
    assert "timed out" in result.stderr


@pytest.mark.asyncio
async def test_bash_timeout_kills_subshell():
    """Timeout must kill commands running inside a subshell."""
    bash = BashTool()
    start = time.monotonic()
    result = await bash.run("(sleep 30)", timeout=2)
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"Subshell took {elapsed:.1f}s with timeout=2"
    assert result.return_code == -1
    assert "timed out" in result.stderr
