# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit test for the reflecting example's LLM reasoner (no network)."""

import sys
from pathlib import Path

_EX = Path(__file__).resolve().parents[4] / "examples" / "memory_bench"
sys.path.insert(0, str(_EX))

import reflecting  # noqa: E402
from nooa_memory.schema import Memory, MemoryType  # noqa: E402

from nooa.unifiedllm import FakeLLMClient, LLMResponse  # noqa: E402


def _resp(content: str) -> LLMResponse:
    return LLMResponse(
        raw_response=None,
        content=content,
        tool_calls=[],
        finish_reason="stop",
        assistant_message={"role": "assistant", "content": content},
    )


def test_reasoner_parses_json_into_reflection_memories():
    payload = (
        '[{"title": "Diet", "content": "User is allergic to peanuts and lactose intolerant."}]'
    )
    reasoner = reflecting.make_llm_reasoner(FakeLLMClient(scripted_responses=[_resp(payload)]))
    out = reasoner([Memory(content="I'm allergic to peanuts.", type=MemoryType.EPISODE)])
    assert len(out) == 1
    assert out[0].type == MemoryType.REFLECTION
    assert out[0].title == "Diet"
    assert "peanut" in out[0].content.lower()


def test_reasoner_tolerates_prose_around_json():
    payload = 'Sure! Here:\n[{"content": "Works at Acme; uses vim."}]\nHope that helps.'
    reasoner = reflecting.make_llm_reasoner(FakeLLMClient(scripted_responses=[_resp(payload)]))
    out = reasoner([Memory(content="x", type=MemoryType.EPISODE)])
    assert len(out) == 1 and "acme" in out[0].content.lower()


def test_reasoner_returns_empty_on_bad_output():
    reasoner = reflecting.make_llm_reasoner(FakeLLMClient(scripted_responses=[_resp("not json")]))
    assert reasoner([Memory(content="x", type=MemoryType.EPISODE)]) == []
