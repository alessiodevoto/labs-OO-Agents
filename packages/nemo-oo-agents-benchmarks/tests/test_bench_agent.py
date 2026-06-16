# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the generic BenchAgent with structured TaskResult output."""

from __future__ import annotations

import pytest
from nemo_oo_agents_benchmarks.agents import bench_agent as bench_agent_module
from nemo_oo_agents_benchmarks.agents.bench_agent import BenchAgent, TaskResult

from nemo_oo_agents.agentdoc import doc
from nemo_oo_agents.unifiedllm import FakeLLMClient


class _FakeShell:
    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.commands: list[str] = []

    async def run(self, command: str):
        self.commands.append(command)
        return None


def test_task_result_model():
    """TaskResult validates required fields with solution_description."""
    r = TaskResult(
        solution_description="Fixed missing URL-encoding in auth.py with quote_plus().",
        evidence="pytest tests/ passed: 5 passed in 1.2s",
        command_to_verify="pytest tests/ -x",
    )
    assert "URL-encoding" in r.solution_description
    assert "pytest" in r.command_to_verify


def test_bench_agent_has_no_verify():
    """BenchAgent does not expose a verify() method."""
    assert not hasattr(BenchAgent, "verify")


def test_bench_agent_has_private_solve_task():
    """BenchAgent uses _solve_task (private) directly; no public solve_task wrapper."""
    assert hasattr(BenchAgent, "_solve_task")


def test_bench_agent_class_exists():
    """BenchAgent can be imported and has expected methods."""
    assert BenchAgent.__name__ == "BenchAgent"
    assert hasattr(BenchAgent, "_run_evaluation")


def test_bench_agent_installs_context_usage_dynamic_block():
    """BenchAgent exposes live context-window usage to the LLM."""
    agent = BenchAgent(llm=FakeLLMClient())

    keys = list(agent.context_manager.keys())

    assert "context_usage" in keys


def test_bench_agent_exposes_context_and_events_apis():
    """BenchAgent exposes context and events APIs so the LLM can act on context-usage hints."""
    agent = BenchAgent(llm=FakeLLMClient())

    agent_doc = doc(agent)

    assert "context:" in agent_doc
    assert "events:" in agent_doc


def test_context_usage_block_includes_collapse_hint():
    """Context usage tells agents how to compact old event history."""
    from nemo_oo_agents.context_blocks.models import ContextWindowStats

    agent = BenchAgent(llm=FakeLLMClient())
    agent.runtime._last_context_stats = ContextWindowStats(
        context_blocks_tokens=100,
        context_blocks_count=2,
        events_tokens=900,
        events_count=12,
        total_tokens=1000,
        max_context_tokens=1000,
        max_event_tokens=1000,
    )

    block = agent._context_usage_block()

    assert "Context usage:" in block
    assert "self.events.collapse(start_tag, end_tag, summary_text)" in block


@pytest.mark.asyncio
async def test_run_evaluation_returns_structured_task_result(monkeypatch, tmp_path):
    shells: list[_FakeShell] = []

    def fake_make_shell(cwd: str):
        shell = _FakeShell(cwd)
        shells.append(shell)
        return shell

    async def fake_solve_task(description: str):
        assert description == "fix the bug"
        return TaskResult(
            solution_description="Fixed the bug.",
            evidence="pytest passed",
            command_to_verify="pytest -q",
        )

    monkeypatch.setattr(bench_agent_module, "_make_shell", fake_make_shell)
    agent = BenchAgent(llm=FakeLLMClient())
    monkeypatch.setattr(agent, "_solve_task", fake_solve_task)

    result = await agent._run_evaluation(
        {"problem_statement": "fix the bug", "working_dir": str(tmp_path)}
    )

    assert result == {
        "response": "pytest -q",
        "success": True,
        "result": {
            "solution_description": "Fixed the bug.",
            "evidence": "pytest passed",
            "command_to_verify": "pytest -q",
        },
    }
    assert shells[-1].cwd == str(tmp_path)
    assert shells[-1].commands


@pytest.mark.asyncio
async def test_run_evaluation_returns_failure_on_exception(monkeypatch, tmp_path):
    def fake_make_shell(cwd: str):
        return _FakeShell(cwd)

    async def fake_solve_task(description: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(bench_agent_module, "_make_shell", fake_make_shell)
    agent = BenchAgent(llm=FakeLLMClient())
    monkeypatch.setattr(agent, "_solve_task", fake_solve_task)

    result = await agent._run_evaluation(
        {"user_message": "fix the bug", "working_dir": str(tmp_path)}
    )

    assert result == {"response": "", "success": False, "error": "boom"}
