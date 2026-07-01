# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-session recall QA — a benchmark where memory is decisive.

A small "personal assistant" is told a set of facts, each in its own session
(short-term context is wiped between sessions). Later — again in fresh sessions —
it is asked about them. The facts are **unique, unguessable tokens**, so an agent
without long-term memory cannot answer them (the information simply isn't in the
current context and can't be derived). This isolates memory's contribution:

* **memory ON**  → recall the fact → correct.
* **memory OFF** → no way to know → "I don't know" / wrong.

Score = fraction of questions answered correctly. The gain (ON − OFF) is the
benchmark's headline number.

Run::

    uv run python examples/memory_bench/recall_qa.py --solver oracle        # offline, deterministic
    uv run python examples/memory_bench/recall_qa.py --solver llm --backend chroma_embedded --embedder litellm
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import MemFacade  # noqa: E402
from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402
from writers import ALL_STRATEGIES, store_footprint, write_memories  # noqa: E402

from nemo_oo_agents import Agent  # noqa: E402
from nemo_oo_agents.memory import MemoryConfig, MemoryManager, MemoryToolsMixin  # noqa: E402
from nemo_oo_agents.memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)
from nemo_oo_agents.unifiedllm import FakeLLMClient  # noqa: E402

log = logging.getLogger("recall_qa")


@dataclass
class Fact:
    statement: str
    question: str
    token: str  # unique answer fragment to grade on


FACTS = [
    Fact("My deployment passphrase is ZEPHYR-7.", "What is my deployment passphrase?", "ZEPHYR-7"),
    Fact(
        "I keep production configs in /srv/atlas/conf.",
        "Where do I keep production configs?",
        "/srv/atlas/conf",
    ),
    Fact(
        "My preferred CI runner is buildkite-nano.",
        "Which CI runner do I prefer?",
        "buildkite-nano",
    ),
    Fact(
        "The on-call escalation code is QX-4419.", "What is the on-call escalation code?", "QX-4419"
    ),
    Fact(
        "Schedule meetings for me in the Pacific/Chatham timezone.",
        "Which timezone should you schedule me in?",
        "Pacific/Chatham",
    ),
    Fact(
        "The staging database is named lighthouse_stg.",
        "What is the staging database name?",
        "lighthouse_stg",
    ),
    Fact(
        "I sign releases with key fingerprint 9F2A-DELTA.",
        "What is my release signing key fingerprint?",
        "9F2A-DELTA",
    ),
    Fact(
        "Our internal package index is at nexus.corp:8081.",
        "Where is our internal package index?",
        "nexus.corp:8081",
    ),
]


def make_assistant_cls(llm, with_memory: bool):
    if with_memory:

        class Assistant(MemoryToolsMixin, Agent, llm=llm):
            async def learn(self, statement: str) -> str:
                """The user tells you a fact to keep for later: {statement}

                Save the key fact to long-term memory with self.remember(...) so you can
                answer questions about it in a future, separate conversation. Reply "ok".

                {doc(self)}
                """
                ...

            async def answer(self, question: str) -> str:
                """Answer the user's question concisely: {question}

                This may rely on something the user told you in an earlier conversation.
                Call self.recall(...) to retrieve it from memory. If — after recalling —
                you genuinely do not know, reply exactly: I don't know.

                {doc(self)}
                """
                ...

        return Assistant

    class Assistant(Agent, llm=llm):
        async def learn(self, statement: str) -> str:
            """The user tells you: {statement}. Reply "ok"."""
            ...

        async def answer(self, question: str) -> str:
            """Answer concisely: {question}
            If you do not know, reply exactly: I don't know.
            """
            ...

    return Assistant


@dataclass
class Result:
    label: str
    correct: int
    total: int
    stored: int = 0
    stored_chars: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / max(1, self.total)


def _sessions():
    # each taught fact is its own one-turn "session"
    return [("", [f.statement]) for f in FACTS]


async def run_condition(*, write, solver, backend, embed, window_k, chunk_chars, top_k) -> Result:
    """write=None -> memory OFF; else ON with that write strategy (retrieval/answer fixed)."""
    on = write is not None
    llm = build_llm() if solver == "llm" else FakeLLMClient()
    agent = make_assistant_cls(llm, with_memory=on and solver == "llm")()
    manager: MemoryManager | None = None
    if on:
        manager = MemoryManager.install(
            agent,
            config=MemoryConfig(
                enabled=True,
                path=":memory:",
                vector=VectorConfig(backend=backend),
                embedding=build_embedding_config(embed),
                retrieval=RetrievalConfig(hops=0, top_k=top_k),
                spontaneous=SpontaneousConfig(query_strategies=("last_message",), top_k=top_k),
                reflection=ReflectionPolicy(enabled=False),
            ),
        )
        # --- WRITE phase (the only thing that varies) ---
        if write == "agent":
            for f in FACTS:
                agent.event_manager.clear()
                if solver == "oracle":
                    MemFacade(manager).remember(f.statement, type="info")
                else:
                    await agent.learn(f.statement)
        else:
            write_memories(
                manager,
                _sessions(),
                strategy=write,
                k=window_k,
                chunk_chars=chunk_chars,
                llm=(llm if solver == "llm" else None),
            )

    label = write if on else "OFF"
    correct = 0
    for f in random.Random(0).sample(FACTS, len(FACTS)):
        agent.event_manager.clear()
        if solver == "oracle":
            recalled = MemFacade(manager).recall(f.question, k=top_k) if manager else []
            ans = next((r for r in recalled if f.token.lower() in r.lower()), "I don't know")
        else:
            try:
                ans = await agent.answer(f.question)
            except Exception as e:  # noqa: BLE001
                ans = f"<error: {e!r}>"
        correct += f.token.lower() in (ans or "").lower()
    stored, stored_chars = store_footprint(manager) if manager is not None else (0, 0)
    if manager is not None:
        log.info("[write=%s] %s | stored=%d", label, manager.memory_stats().summary(), stored)
        manager.uninstall()
    return Result(label, correct, len(FACTS), stored, stored_chars)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--solver", choices=["oracle", "llm", "auto"], default="auto")
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    p.add_argument(
        "--write",
        nargs="+",
        default=["agent"],
        choices=list(ALL_STRATEGIES),
        help="write-op strategies to compare (the ablation)",
    )
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--window-k", type=int, default=8)
    p.add_argument("--chunk-chars", type=int, default=400)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.verbose:
        logging.getLogger("nemo_oo_agents.memory").setLevel(logging.DEBUG)

    solver = args.solver
    if solver == "auto":
        solver = "llm" if has_llm_creds() else "oracle"
    writes = list(args.write)
    if solver == "oracle" and "llm-summary" in writes:
        writes = [w for w in writes if w != "llm-summary"]
        print("(oracle: skipping llm-summary — needs an LLM)")
    print(
        f"solver={solver}  backend={args.backend}  embedder={args.embedder}  "
        f"facts={len(FACTS)}  write={writes}\n"
    )

    common = {
        "solver": solver,
        "backend": args.backend,
        "embed": args.embedder,
        "window_k": args.window_k,
        "chunk_chars": args.chunk_chars,
        "top_k": args.top_k,
    }

    async def go():
        off = await run_condition(write=None, **common)
        ons = [await run_condition(write=w, **common) for w in writes]
        return off, ons

    off, ons = asyncio.run(go())

    print("\n" + "=" * 64)
    print("CROSS-SESSION RECALL QA — write-op ablation (retrieval+answer fixed)")
    print("=" * 64)
    print(f"  {'write':12s} {'accuracy':>12s} {'stored':>8s} {'chars':>8s}")
    print(f"  {'OFF':12s} {f'{off.correct}/{off.total} ({off.accuracy:.0%})':>12s}")
    for r in ons:
        print(
            f"  {r.label:12s} {f'{r.correct}/{r.total} ({r.accuracy:.0%})':>12s} "
            f"{r.stored:>8d} {r.stored_chars:>8d}"
        )
    agent_r = next((r for r in ons if r.label == "agent"), None)
    det = [r for r in ons if r.label != "agent"]
    if agent_r and det:
        best = max(det, key=lambda r: r.accuracy)
        print(
            f"  --> agent {agent_r.accuracy:.0%} vs best deterministic "
            f"({best.label}) {best.accuracy:.0%}: {agent_r.accuracy - best.accuracy:+.0%} acc; "
            f"storage {agent_r.stored} vs {best.stored} memories"
        )
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
