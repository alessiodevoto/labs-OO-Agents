# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for nemo_oo_agents_benchmarks.runner — smoke tests for CLI and imports."""

import subprocess
import sys


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
