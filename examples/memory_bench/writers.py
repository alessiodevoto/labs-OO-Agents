# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write-op strategies for the memory benchmarks.

Ablation: hold retrieval + answering fixed and vary ONLY how memories are written,
to test whether the agent's self-authored writing beats a deterministic harness
rule. Strategies:

* ``agent``       — the agent writes/curates its own memories (each benchmark's own
                    agentic path; NOT handled here).
* ``raw``         — the harness stores every turn verbatim (max recall, no curation).
* ``window``      — the harness stores only the last K turns (recency rule).
* ``chunk``       — fixed-size overlapping chunks of each session (standard RAG ingest).
* ``llm-summary`` — the harness summarizes each session with ONE LLM call (no agent
                    loop) — an "agency control": is the agent's in-loop judgment
                    better than a fixed one-shot extractor?

A "session" is ``(date, [turn_text, ...])``. All deterministic writers go straight
to ``manager.store`` (bypassing dedup/agent), so retrieval is the only shared stage.
"""

from __future__ import annotations

from collections.abc import Callable

from nemo_oo_agents.memory.schema import Memory, MemoryType

Session = tuple[str, list[str]]

DETERMINISTIC = ("raw", "window", "chunk", "llm-summary")
ALL_STRATEGIES = ("agent", *DETERMINISTIC)


def _store(
    manager, content: str, *, date: str = "", mtype: MemoryType = MemoryType.EPISODE
) -> None:
    text = content.strip()
    if not text:
        return
    m = Memory(content=text, type=mtype, source_task_ref=date or None, tags=[date] if date else [])
    manager.store.add(m, manager.embedder.embed(m.embedding_text()))


def write_raw(manager, sessions: list[Session]) -> int:
    n = 0
    for date, turns in sessions:
        for t in turns:
            if t.strip():
                _store(manager, t, date=date)
                n += 1
    return n


def write_window(manager, sessions: list[Session], k: int) -> int:
    flat = [(date, t) for date, turns in sessions for t in turns if t.strip()]
    for date, t in flat[-k:]:
        _store(manager, t, date=date)
    return min(k, len(flat))


def write_chunk(
    manager, sessions: list[Session], chunk_chars: int = 600, overlap: int = 100
) -> int:
    n = 0
    step = max(1, chunk_chars - overlap)
    for date, turns in sessions:
        text = "\n".join(t for t in turns if t.strip())
        for i in range(0, max(1, len(text)), step):
            chunk = text[i : i + chunk_chars]
            if chunk.strip():
                _store(manager, chunk, date=date)
                n += 1
            if i + chunk_chars >= len(text):
                break
    return n


def write_llm_summary(manager, sessions: list[Session], llm) -> int:
    n = 0
    for date, turns in sessions:
        text = "\n".join(t for t in turns if t.strip())
        if not text:
            continue
        prompt = (
            "Summarize the durable facts, preferences, decisions, and events from this "
            "conversation session into a concise note. Keep exact names, numbers, dates, and "
            "specifics. Session:\n" + text
        )
        try:
            summary = (llm.call([{"role": "user", "content": prompt}]).content or "").strip()
        except Exception:  # noqa: BLE001
            summary = text[:500]
        _store(
            manager, f"[{date}] {summary}" if date else summary, date=date, mtype=MemoryType.INFO
        )
        n += 1
    return n


def write_memories(
    manager,
    sessions: list[Session],
    *,
    strategy: str,
    k: int = 30,
    chunk_chars: int = 600,
    llm=None,
) -> int:
    """Deterministic harness ingestion. ('agent' is handled by the caller.)"""
    if strategy == "raw":
        return write_raw(manager, sessions)
    if strategy == "window":
        return write_window(manager, sessions, k)
    if strategy == "chunk":
        return write_chunk(manager, sessions, chunk_chars)
    if strategy == "llm-summary":
        if llm is None:
            raise ValueError("llm-summary requires an llm")
        return write_llm_summary(manager, sessions, llm)
    raise ValueError(f"deterministic writer got unknown/agent strategy: {strategy!r}")


def store_footprint(manager) -> tuple[int, int]:
    """(#active memories, total content chars) — the storage cost of a write strategy."""
    mems = manager.store.all_memories()
    return len(mems), sum(len(m.content) for m in mems)


# convenience: build a deterministic writer bound to (k, chunk_chars, llm)
def make_writer(strategy: str, *, k: int = 30, chunk_chars: int = 600, llm=None) -> Callable:
    return lambda manager, sessions: write_memories(
        manager, sessions, strategy=strategy, k=k, chunk_chars=chunk_chars, llm=llm
    )
