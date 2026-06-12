# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Terminal-Bench verification gate.

TB grades with held-out tests uploaded only at verifier time, and TB tasks are
not git repos. So the gate's single invariant is: the agent ran a passing
acceptance check via ``self.verify()`` this session, else the return is bounced.
"""

import pytest
from nemo_oo_agents_benchmarks.agents.terminal_bench2 import TerminalBench2Agent


class _Out(str):
    """Terminal output stub: a str subclass carrying a returncode."""

    def __new__(cls, text, returncode=0):
        o = super().__new__(cls, text)
        o.returncode = returncode
        return o


class _Terminal:
    def __init__(self, output):
        self.output = output
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return self.output


class _Agent:
    """Stub exercising the TB gate without a live CodeAct loop."""

    solve_task = TerminalBench2Agent.solve_task
    verify = TerminalBench2Agent.verify
    _verification_gate = TerminalBench2Agent._verification_gate
    _parse_check_outcome = staticmethod(TerminalBench2Agent._parse_check_outcome)
    _MAX_VERIFY_BOUNCES = TerminalBench2Agent._MAX_VERIFY_BOUNCES

    def __init__(self, *, result, verify_on_attempt=None, verify_passed=True, check_output=None):
        self.result = result
        self._last_verify = None
        self._verify_bounces = 0
        self._pending_nudge = None
        self._verify_on_attempt = verify_on_attempt
        self._verify_passed = verify_passed
        self.terminal = _Terminal(check_output if check_output is not None else _Out("ok", 0))
        self.descriptions = []
        self.nudges = []

    async def _solve_task(self, description):
        attempt = len(self.descriptions)
        self.descriptions.append(description)
        if self._pending_nudge is not None:
            self.nudges.append(self._pending_nudge)
            self._pending_nudge = None
        if self._verify_on_attempt is not None and attempt >= self._verify_on_attempt:
            self._last_verify = {
                "passed": self._verify_passed,
                "n_passed": 1 if self._verify_passed else 0,
                "n_failed": 0 if self._verify_passed else 1,
                "returncode": 0 if self._verify_passed else 1,
                "cmd": "make",
                "preview": "",
            }
        return self.result


# ── parser ───────────────────────────────────────────────────────────


def test_parse_check_green_rc0():
    o = TerminalBench2Agent._parse_check_outcome(_Out("Build succeeded", 0))
    assert o["passed"] is True


def test_parse_check_red_nonzero_rc():
    o = TerminalBench2Agent._parse_check_outcome(_Out("Build succeeded", 1))
    assert o["passed"] is False


def test_parse_check_red_on_failure_marker():
    o = TerminalBench2Agent._parse_check_outcome(_Out("1 failed, 2 passed", 0))
    assert o["passed"] is False and o["n_failed"] == 1


def test_parse_check_red_on_traceback():
    o = TerminalBench2Agent._parse_check_outcome(_Out("Traceback (most recent call last): ...", 0))
    assert o["passed"] is False


# ── gate ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_opens_when_verify_passed():
    agent = _Agent(result="done", verify_on_attempt=0, verify_passed=True)
    out = await agent.solve_task("task")
    assert out == "done"
    assert agent.descriptions == ["task"]  # no bounce


@pytest.mark.asyncio
async def test_gate_bounces_when_no_verify():
    agent = _Agent(result="done", verify_on_attempt=None)
    out = await agent.solve_task("task")
    assert out == "done"  # honest unverified result after exhausting bounces
    assert agent._verify_bounces == agent._MAX_VERIFY_BOUNCES
    assert any("never ran an acceptance check" in n for n in agent.nudges)


@pytest.mark.asyncio
async def test_gate_bounces_then_opens_after_verify():
    agent = _Agent(result="done", verify_on_attempt=1, verify_passed=True)
    out = await agent.solve_task("task")
    assert out == "done"
    assert agent._verify_bounces == 1  # one bounce, then verified
    assert any("never ran an acceptance check" in n for n in agent.nudges)


@pytest.mark.asyncio
async def test_gate_bounces_on_failed_verify():
    agent = _Agent(result="done", verify_on_attempt=0, verify_passed=False)
    out = await agent.solve_task("task")
    assert out == "done"
    assert agent._verify_bounces == agent._MAX_VERIFY_BOUNCES
    assert any("last `verify()` FAILED" in n for n in agent.nudges)


@pytest.mark.asyncio
async def test_verify_records_outcome_and_runs_command():
    agent = _Agent(result="done", check_output=_Out("All tests passed", 0))
    msg = await agent.verify("pytest -q")
    assert "PASSED" in msg
    assert agent.terminal.commands == ["pytest -q"]
    assert agent._last_verify["passed"] is True
