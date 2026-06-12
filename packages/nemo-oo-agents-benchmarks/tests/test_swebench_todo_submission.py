# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the SWE-bench in-place submission contract.

The harbor verifier grades the working tree directly (it runs the task's own
test script against /testbed) — it never reads a returned patch. So solve_task
returns only a short status string; the agent's file edits ARE the submission.
"""

import pytest
from nemo_oo_agents_benchmarks.agents.swebench_todo import SWEBenchTodoAgent


class _RunResult:
    def __init__(self, text: str):
        self.text = text


class _Shell:
    def __init__(self, diff: str):
        self.diff = diff
        self.commands: list[str] = []

    async def run(self, command: str, **kwargs):
        self.commands.append(command)
        return _RunResult(self.diff)


class _Agent:
    """Stub exercising the verification gate without a live CodeAct loop.

    ``solve_task`` and the gate helpers are bound from the real class. The
    stubbed ``_solve_task`` records each (re)invocation and optionally flips
    ``_last_verify`` to simulate the agent verifying on a given attempt.
    """

    solve_task = SWEBenchTodoAgent.solve_task
    _verification_gate = SWEBenchTodoAgent._verification_gate
    _MAX_VERIFY_BOUNCES = SWEBenchTodoAgent._MAX_VERIFY_BOUNCES

    def __init__(
        self, *, result, worktree_diff: str = "", verify_on_attempt=None, verify_passed=True
    ):
        self.result = result
        self.shell = _Shell(worktree_diff)
        self._last_verify = None
        self._verify_bounces = 0
        self._pending_nudge = None
        self._reopened = 0
        # Simulate the agent calling self.verify() on a given (0-indexed) loop
        # attempt: after that attempt, _last_verify is set with passed=...
        self._verify_on_attempt = verify_on_attempt
        self._verify_passed = verify_passed
        self.descriptions: list[str] = []
        self.nudges: list[str] = []

    def _reopen_verify_todos(self) -> None:
        self._reopened += 1

    async def _solve_task(self, description: str, response_format: str = ""):
        attempt = len(self.descriptions)
        self.descriptions.append(description)
        # Mirror the real method: a pending nudge is surfaced then cleared.
        if self._pending_nudge is not None:
            self.nudges.append(self._pending_nudge)
            self._pending_nudge = None
        if self._verify_on_attempt is not None and attempt >= self._verify_on_attempt:
            self._last_verify = {
                "passed": self._verify_passed,
                "n_passed": 3 if self._verify_passed else 0,
                "n_failed": 0 if self._verify_passed else 2,
                "returncode": 0 if self._verify_passed else 1,
                "cmd": "pytest tests/test_x.py",
                "preview": "",
            }
        return self.result


@pytest.mark.asyncio
async def test_diff_format_returns_status_when_gate_satisfied():
    """response_format='diff' returns the agent's status string (not a patch)
    once the gate is satisfied: worktree has edits AND a green verify ran."""
    agent = _Agent(
        result="done",
        worktree_diff="diff --git a/foo.py b/foo.py\n",
        verify_on_attempt=0,
        verify_passed=True,
    )
    out = await agent.solve_task("task", "diff")
    assert out == "done"
    assert agent.descriptions == ["task"]  # no bounce needed


@pytest.mark.asyncio
async def test_diff_format_defaults_status_when_result_is_none():
    """A None result on a diff task still passes the gate once verified green."""
    agent = _Agent(
        result=None,
        worktree_diff="diff --git a/foo.py b/foo.py\n",
        verify_on_attempt=0,
        verify_passed=True,
    )
    out = await agent.solve_task("task", "diff")
    assert out == "done"


@pytest.mark.asyncio
async def test_gate_checks_worktree_for_edits():
    """The gate inspects `git diff HEAD` to confirm the agent actually edited files."""
    agent = _Agent(
        result="done",
        worktree_diff="diff --git a/foo.py b/foo.py\n",
        verify_on_attempt=0,
        verify_passed=True,
    )
    await agent.solve_task("task", "diff")
    assert "git diff HEAD" in agent.shell.commands


@pytest.mark.asyncio
async def test_non_diff_format_passes_result_through_without_gate():
    """Non-diff response formats bypass the gate — result is returned as-is, no git diff."""
    agent = _Agent(result="some answer", worktree_diff="")
    out = await agent.solve_task("task", "code")
    assert out == "some answer"
    assert agent.shell.commands == []  # no gate / git diff for non-diff formats


# ── Verification gate ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_bounces_when_no_verify_then_returns_after_verify():
    """Agent that didn't verify on attempt 0 gets bounced; once it verifies
    green on the next attempt, the gate opens."""
    agent = _Agent(
        result="done",
        worktree_diff="diff --git a/foo.py b/foo.py\n",
        verify_on_attempt=1,
        verify_passed=True,
    )
    out = await agent.solve_task("task", "diff")
    assert out == "done"
    # bounced exactly once: two _solve_task invocations, 2nd carries the nudge.
    assert len(agent.descriptions) == 2
    assert agent.nudges and "never ran the failing tests" in agent.nudges[0]
    assert agent._verify_bounces == 1
    assert agent._reopened == 1


@pytest.mark.asyncio
async def test_gate_bounces_on_empty_worktree():
    """No edits → the nudge names the empty-worktree problem; exhausts bounces."""
    agent = _Agent(result="done", worktree_diff="", verify_on_attempt=None)
    out = await agent.solve_task("task", "diff")
    assert out == "done"  # honest unverified result stands after bounces
    assert agent._verify_bounces == agent._MAX_VERIFY_BOUNCES
    assert any("NO edits" in n for n in agent.nudges)


@pytest.mark.asyncio
async def test_gate_bounces_on_failed_verify():
    """A red verify is rejected — a passing manual script can't open the gate."""
    agent = _Agent(
        result="done",
        worktree_diff="diff --git a/foo.py b/foo.py\n",
        verify_on_attempt=0,
        verify_passed=False,
    )
    out = await agent.solve_task("task", "diff")
    assert out == "done"
    assert agent._verify_bounces == agent._MAX_VERIFY_BOUNCES
    assert any("last `verify()` FAILED" in n for n in agent.nudges)


@pytest.mark.asyncio
async def test_gate_exhausts_bounces_and_returns_honestly():
    """Agent that never verifies is bounced MAX times, then the unverified
    result is returned (no infinite loop)."""
    agent = _Agent(
        result="done", worktree_diff="diff --git a/foo.py b/foo.py\n", verify_on_attempt=None
    )
    out = await agent.solve_task("task", "diff")
    assert out == "done"
    # MAX_VERIFY_BOUNCES bounces => MAX+1 total invocations
    assert len(agent.descriptions) == agent._MAX_VERIFY_BOUNCES + 1


def test_parse_test_outcome_pytest_green():
    """A pytest green summary parses as passed with the correct pass count."""
    a = _Agent(result="done")
    o = SWEBenchTodoAgent._parse_test_outcome(a, _RunResult("=== 3 passed in 0.4s ==="))
    assert o["passed"] is True and o["n_passed"] == 3


def test_parse_test_outcome_pytest_red():
    """A pytest summary with failures parses as not-passed with the fail count."""
    a = _Agent(result="done")
    o = SWEBenchTodoAgent._parse_test_outcome(a, _RunResult("=== 1 failed, 2 passed in 0.4s ==="))
    assert o["passed"] is False and o["n_failed"] == 1


def test_parse_test_outcome_no_tests_ran_is_not_pass():
    """`collected 0 items` is not a pass — the gate must not open on a no-op run."""
    a = _Agent(result="done")
    o = SWEBenchTodoAgent._parse_test_outcome(a, _RunResult("collected 0 items"))
    assert o["passed"] is False
