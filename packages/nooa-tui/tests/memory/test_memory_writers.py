# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the deterministic write-op strategies (no network)."""

import sys
from pathlib import Path

import pytest

_EX = Path(__file__).resolve().parents[4] / "examples" / "memory_bench"
sys.path.insert(0, str(_EX))

import writers  # noqa: E402
from nooa_tui.memory.embeddings import HashingEmbedder  # noqa: E402
from nooa_tui.memory.store import MemoryStore  # noqa: E402

from nooa.unifiedllm import FakeLLMClient, LLMResponse  # noqa: E402


class _Mgr:
    """Minimal stand-in: writers only need .store and .embedder."""

    def __init__(self):
        self.embedder = HashingEmbedder(dim=64)
        self.store = MemoryStore(":memory:")


def _sessions():
    return [
        ("d1", ["user: hi", "assistant: hello there"]),
        ("d2", ["user: the escalation code is QX-9", "assistant: noted"]),
    ]


def test_write_raw_stores_every_turn():
    m = _Mgr()
    n = writers.write_memories(m, _sessions(), strategy="raw")
    assert n == 4 and m.store.count() == 4
    m.store.close()


def test_write_window_keeps_last_k():
    m = _Mgr()
    n = writers.write_memories(m, _sessions(), strategy="window", k=2)
    assert n == 2 and m.store.count() == 2
    assert any("QX-9" in x.content for x in m.store.all_memories())  # last turns kept
    m.store.close()


def test_write_chunk_produces_chunks():
    m = _Mgr()
    n = writers.write_memories(m, _sessions(), strategy="chunk", chunk_chars=12)
    assert n >= 2 and m.store.count() == n
    m.store.close()


def test_store_footprint():
    m = _Mgr()
    writers.write_memories(m, _sessions(), strategy="raw")
    cnt, chars = writers.store_footprint(m)
    assert cnt == 4 and chars > 0
    m.store.close()


def test_llm_summary_requires_llm():
    m = _Mgr()
    with pytest.raises(ValueError, match="requires an llm"):
        writers.write_memories(m, _sessions(), strategy="llm-summary", llm=None)
    m.store.close()


def test_llm_summary_one_memory_per_session():
    def resp(c):
        return LLMResponse(
            raw_response=None,
            content=c,
            tool_calls=[],
            finish_reason="stop",
            assistant_message={"role": "assistant", "content": c},
        )

    fake = FakeLLMClient(scripted_responses=[resp("summary one"), resp("summary two")])
    m = _Mgr()
    n = writers.write_memories(m, _sessions(), strategy="llm-summary", llm=fake)
    assert n == 2 and m.store.count() == 2  # one summary memory per session
    m.store.close()


def test_unknown_or_agent_strategy_raises():
    m = _Mgr()
    with pytest.raises(ValueError):
        writers.write_memories(m, _sessions(), strategy="agent")  # 'agent' handled by caller
    m.store.close()
