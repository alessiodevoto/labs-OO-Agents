# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the SWE-bench in-place submission contract.

The harbor verifier grades the working tree directly (it runs the task's own
test script against /testbed) — it never reads a returned patch. So solve_task
returns only a short status string; the agent's file edits ARE the submission.
"""

import logging

import pytest
from nemo_oo_agents_benchmarks.agents.swebench_todo import SWEBenchTodoAgent


class _RunResult:
    def __init__(self, text: str):
        self.text = text


class _Shell:
    def __init__(self, diff: str):
        self.diff = diff
        self.commands: list[str] = []

    async def run(self, command: str):
        self.commands.append(command)
        return _RunResult(self.diff)


class _Agent:
    solve_task = SWEBenchTodoAgent.solve_task

    def __init__(self, *, result, worktree_diff: str = ""):
        self.result = result
        self.shell = _Shell(worktree_diff)

    async def _solve_task(self, description: str, response_format: str = ""):
        return self.result


@pytest.mark.asyncio
async def test_diff_format_returns_status_not_a_patch():
    """For response_format='diff' the wrapper returns the agent's status string,
    NOT a captured diff — grading happens in the worktree."""
    agent = _Agent(result="done", worktree_diff="diff --git a/foo.py b/foo.py\n")
    out = await agent.solve_task("task", "diff")
    assert out == "done"


@pytest.mark.asyncio
async def test_diff_format_defaults_status_when_result_is_none():
    agent = _Agent(result=None, worktree_diff="diff --git a/foo.py b/foo.py\n")
    out = await agent.solve_task("task", "diff")
    assert out == "done"


@pytest.mark.asyncio
async def test_diff_format_checks_worktree_for_edits():
    """The wrapper sanity-checks the worktree (git diff HEAD) so an empty tree
    is observable, but does not fabricate or alter the submission."""
    agent = _Agent(result="done", worktree_diff="diff --git a/foo.py b/foo.py\n")
    await agent.solve_task("task", "diff")
    assert "git diff HEAD" in agent.shell.commands


@pytest.mark.asyncio
async def test_empty_worktree_warns_but_still_returns_status(caplog):
    agent = _Agent(result="done", worktree_diff="")
    with caplog.at_level(logging.WARNING):
        out = await agent.solve_task("task", "diff")
    assert out == "done"
    assert any("no edits" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_non_diff_format_passes_result_through_without_worktree_check():
    agent = _Agent(result="some answer", worktree_diff="")
    out = await agent.solve_task("task", "code")
    assert out == "some answer"
    assert agent.shell.commands == []  # no git diff for non-diff formats
