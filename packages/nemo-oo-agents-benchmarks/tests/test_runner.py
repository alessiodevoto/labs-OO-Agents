# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for nemo_oo_agents_benchmarks.runner — smoke tests for CLI and imports."""

import subprocess
import sys

from nemo_oo_agents_benchmarks.runner import _inject_tools


class _FakeAgent:
    """Minimal stand-in for an agent that accepts injected tool attributes."""


def test_inject_tools_swebench_uses_working_dir():
    """--working-dir must reach the SWEBenchLocalTools constructor."""
    agent = _FakeAgent()
    _inject_tools(agent, frozenset({"swebench"}), "swebench/pro", "/app")
    assert agent.swebench._workdir == "/app"


def test_inject_tools_swebench_defaults_when_no_working_dir():
    """Without --working-dir the swebench tools fall back to /testbed."""
    agent = _FakeAgent()
    _inject_tools(agent, frozenset({"swebench"}), "swebench/basic", None)
    assert agent.swebench._workdir == "/testbed"


def test_inject_tools_terminal_uses_working_dir():
    """--working-dir must reach the TerminalBenchTools constructor."""
    agent = _FakeAgent()
    _inject_tools(agent, frozenset({"terminal"}), "terminal-bench-2", "/custom/dir")
    assert agent.terminal._workdir == "/custom/dir"


def test_inject_tools_terminal_defaults_when_no_working_dir():
    """Without --working-dir the terminal tools fall back to /app."""
    agent = _FakeAgent()
    _inject_tools(agent, frozenset({"terminal"}), "terminal-bench-1", None)
    assert agent.terminal._workdir == "/app"


def test_inject_tools_terminal_autoinjected_for_terminal_bench_agents():
    """terminal-bench agents get terminal tools even without --tools."""
    agent = _FakeAgent()
    _inject_tools(agent, frozenset(), "terminal-bench-2", "/app")
    assert hasattr(agent, "terminal")


def test_runner_help():
    """Runner module must be importable and show help."""
    result = subprocess.run(
        [sys.executable, "-m", "nemo_oo_agents_benchmarks.runner", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--instruction" in result.stdout
    assert "--model" in result.stdout
    assert "--agent-type" in result.stdout


def test_runner_module_entrypoint_help():
    """``python -m nemo_oo_agents_benchmarks`` must also show help."""
    result = subprocess.run(
        [sys.executable, "-m", "nemo_oo_agents_benchmarks", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--instruction" in result.stdout


def test_runner_invalid_agent_type():
    """Unknown agent type must exit non-zero with a clear message."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nemo_oo_agents_benchmarks.runner",
            "--instruction",
            "Fix the bug",
            "--model",
            "openai/test-model",
            "--agent-type",
            "no_such_agent",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "no_such_agent" in combined or "unknown" in combined.lower()


def test_agent_classes_importable():
    """Both SWEBench agent classes must import cleanly."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nemo_oo_agents_benchmarks.agents.swebench_basic import SWEBenchBasicAgent; "
            "from nemo_oo_agents_benchmarks.agents.swebench_opt1 import SWEBenchOpt1Agent; "
            "print('OK')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_tools_importable():
    """SWEBenchLocalTools must import cleanly."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nemo_oo_agents_benchmarks.tools import SWEBenchLocalTools; print('OK')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_tau_bench_unregistered():
    """tau-bench must NOT be registered: nothing provides self.taubench, so it
    would crash on the first context render (see #343)."""
    from nemo_oo_agents_benchmarks.agents import AGENT_CLASSES

    assert "tau-bench" not in AGENT_CLASSES


def test_agent_registry_complete():
    """AGENT_CLASSES must contain baseline and SWE-bench agents."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nemo_oo_agents_benchmarks.agents import AGENT_CLASSES; "
            "assert 'baseline' in AGENT_CLASSES; "
            "assert 'swebench/basic' in AGENT_CLASSES; "
            "assert 'swebench/opt1' in AGENT_CLASSES; "
            "print('OK')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
