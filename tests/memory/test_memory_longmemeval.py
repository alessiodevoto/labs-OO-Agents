# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the LongMemEval demo's LLM reconciler (no network)."""

import sys
from pathlib import Path

_EX = Path(__file__).resolve().parents[2] / "examples" / "memory_bench"
sys.path.insert(0, str(_EX))

import longmemeval  # noqa: E402

from nemo_oo_agents.memory.schema import Memory, MemoryType  # noqa: E402
from nemo_oo_agents.unifiedllm import FakeLLMClient, LLMResponse  # noqa: E402


def _resp(content: str) -> LLMResponse:
    return LLMResponse(
        raw_response=None,
        content=content,
        tool_calls=[],
        finish_reason="stop",
        assistant_message={"role": "assistant", "content": content},
    )


def _cluster():
    return [
        Memory(content="My 5K personal best is 27:00.", type=MemoryType.INFO),
        Memory(content="My 5K personal best is now 25:50.", type=MemoryType.INFO),
    ]


def test_reconciler_supersedes_outdated_and_keeps_current():
    payload = '{"current": "Current 5K personal best: 25:50.", "outdated": [0]}'
    rec = longmemeval.make_llm_reconciler(FakeLLMClient(scripted_responses=[_resp(payload)]))
    cluster = _cluster()
    consolidated, archive = rec(cluster)
    assert consolidated is not None and "25:50" in consolidated.content
    assert consolidated.type == MemoryType.REFLECTION or consolidated.type == MemoryType.INFO
    assert archive == [cluster[0].id]  # the older value is archived


def test_reconciler_noop_when_consistent():
    rec = longmemeval.make_llm_reconciler(
        FakeLLMClient(scripted_responses=[_resp('{"current":"","outdated":[]}')])
    )
    consolidated, archive = rec(_cluster())
    assert consolidated is None and archive == []


def test_reconciler_handles_bad_output():
    rec = longmemeval.make_llm_reconciler(FakeLLMClient(scripted_responses=[_resp("not json")]))
    assert rec(_cluster()) == (None, [])


def test_categories_are_consolidation_oriented():
    assert "knowledge-update" in longmemeval.CONSOLIDATION_CATEGORIES
    assert "multi-session" in longmemeval.CONSOLIDATION_CATEGORIES
