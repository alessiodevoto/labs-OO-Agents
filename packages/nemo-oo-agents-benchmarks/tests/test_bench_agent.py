# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the generic BenchAgent."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from nemo_oo_agents_benchmarks.agents.bench_agent import BenchAgent


@dataclass
class _RunResult:
    text: str
    stdout: str = ""
    returncode: int = 0

    def __str__(self):
        return self.text


def _make_agent() -> BenchAgent:
    a = BenchAgent.__new__(BenchAgent)
    a._last_verify = None
    a._verify_bounces = 0
    a._pending_nudge = None
    a.terminal = None
    return a


# ── _parse_test_outcome ──────────────────────────────────────────────


def test_parse_pytest_green():
    """Pytest green summary parses as passed."""
    a = _make_agent()
    o = a._parse_test_outcome(_RunResult("=== 3 passed in 0.4s ===", returncode=0))
    assert o["passed"] is True and o["n_passed"] == 3


def test_parse_pytest_red():
    """Pytest summary with failures parses as not-passed."""
    a = _make_agent()
    o = a._parse_test_outcome(_RunResult("=== 1 failed, 2 passed in 0.4s ===", returncode=1))
    assert o["passed"] is False and o["n_failed"] == 1


def test_parse_no_tests_ran():
    """`collected 0 items` is not a pass."""
    a = _make_agent()
    o = a._parse_test_outcome(_RunResult("collected 0 items", returncode=0))
    assert o["passed"] is False


def test_parse_nonzero_rc_is_fail():
    """Non-zero rc counts as failure."""
    a = _make_agent()
    o = a._parse_test_outcome(_RunResult("all good", returncode=1))
    assert o["passed"] is False


def test_parse_rc0_no_markers_is_pass():
    """rc=0 with no failure markers is a pass."""
    a = _make_agent()
    o = a._parse_test_outcome(_RunResult("build complete", returncode=0))
    assert o["passed"] is True


def test_error_colon_not_in_markers():
    """Bare 'error:' is NOT a marker — ValueError: in output doesn't false-negative."""
    a = _make_agent()
    o = a._parse_test_outcome(
        _RunResult("=== 5 passed in 1.2s ===\nValueError: old warning", returncode=0)
    )
    assert o["passed"] is True


# ── Verification gate ────────────────────────────────────────────────


def test_gate_no_verify():
    """No verify run → rejection."""
    a = _make_agent()
    a._last_verify = None
    nudge = a._verification_gate()
    assert nudge is not None
    assert "never ran" in nudge


def test_gate_passes():
    """Passing verify → gate opens."""
    a = _make_agent()
    a._last_verify = {
        "passed": True,
        "n_passed": 3,
        "n_failed": 0,
        "cmd": "pytest",
        "returncode": 0,
    }
    nudge = a._verification_gate()
    assert nudge is None


def test_gate_failed_verify():
    """Failed verify → rejection."""
    a = _make_agent()
    a._last_verify = {
        "passed": False,
        "n_passed": 0,
        "n_failed": 1,
        "cmd": "make test",
        "returncode": 1,
    }
    nudge = a._verification_gate()
    assert nudge is not None
    assert "FAILED" in nudge


# ── verify() tool ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_records_outcome():
    """verify() records the outcome in _last_verify."""
    a = _make_agent()
    a.shell = AsyncMock()
    a.shell.run = AsyncMock(return_value=_RunResult("=== 2 passed in 0.1s ===", returncode=0))
    msg = await a.verify("pytest -x")
    assert "PASSED" in msg
    assert a._last_verify is not None
    assert a._last_verify["passed"] is True


@pytest.mark.asyncio
async def test_verify_uses_terminal_if_available():
    """When self.terminal is set, verify() uses terminal.execute."""
    a = _make_agent()
    a.terminal = AsyncMock()
    a.terminal.execute = AsyncMock(return_value=_RunResult("ok", returncode=0))
    msg = await a.verify("make test")
    a.terminal.execute.assert_called_once_with("make test")
    assert "PASSED" in msg
