# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Does *reflection* (offline consolidation) actually help?

This isolates the consolidation step. The agent accumulates many small **episodic**
memories scattered across sessions (its experience log). Then we answer **synthesis**
questions ("tell me everything about X") under a realistic, small retrieval budget
(top_k=3) — where plain retrieval can only fetch a few of the scattered pieces.

Two conditions, BOTH with memory ON (so the only difference is reflection):

* **reflect OFF** — answer from the raw episodes (top_k retrieval).
* **reflect ON**  — first run `manager.reflect()`, whose generative step uses an LLM
  reasoner to consolidate the scattered episodes into a few compact `reflection`
  memories; then answer. One consolidated memory now packs the whole synthesis, so
  it fits the retrieval budget.

Metric: answer **completeness** = fraction of each topic's gold facts present in the
answer. The headline is the completeness gain of reflect ON over reflect OFF.

Requires LLM credentials (the reasoner and answerer are real). Run::

    uv run python examples/memory_bench/reflecting.py --backend chroma_embedded --embedder litellm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402

from nemo_oo_agents import Agent  # noqa: E402
from nemo_oo_agents.memory import MemoryConfig, MemoryManager  # noqa: E402
from nemo_oo_agents.memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)
from nemo_oo_agents.memory.schema import Memory, MemoryType  # noqa: E402

log = logging.getLogger("reflecting")

# Topics with several scattered facts each; tokens are what we grade completeness on.
TOPICS = {
    "diet and food preferences": [
        ("I'm allergic to peanuts.", "peanut"),
        ("I'm lactose intolerant.", "lactose"),
        ("I only drink dark-roast coffee.", "dark"),
        ("I'm vegetarian on weekdays.", "vegetarian"),
        ("I really dislike cilantro.", "cilantro"),
    ],
    "work setup": [
        ("I work at Acme Robotics.", "acme"),
        ("My manager is Dana Lindqvist.", "dana"),
        ("I use the vim editor.", "vim"),
        ("Our team channel is #atlas-eng.", "atlas-eng"),
        ("We deploy on Fridays only.", "friday"),
    ],
}
QUESTIONS = {
    "What do you know about my diet and food preferences? List everything.": "diet and food preferences",
    "Summarize my work setup. List everything you know.": "work setup",
}


def make_llm_reasoner(llm):
    """LLM-backed abstraction step: consolidate scattered episodes into summary memories."""

    def reasoner(memories: list[Memory]) -> list[Memory]:
        bullets = "\n".join(f"- {m.content}" for m in memories)
        prompt = (
            "You are consolidating your episodic memories. Group the related "
            "ones by topic and write a FEW consolidated summary notes, each capturing ALL the "
            "details of that topic in one self-contained note. Respond ONLY as a JSON array of "
            '{"title": str, "content": str}.\n\nEpisodes:\n' + bullets
        )
        try:
            txt = llm.call([{"role": "user", "content": prompt}]).content or ""
            data = json.loads(txt[txt.find("[") : txt.rfind("]") + 1])
        except Exception as e:  # noqa: BLE001
            log.warning("reasoner failed: %r", e)
            return []
        out: list[Memory] = []
        for d in data:
            content = (d or {}).get("content") if isinstance(d, dict) else None
            if content:
                out.append(
                    Memory(
                        content=content,
                        title=(d.get("title") or None),
                        type=MemoryType.REFLECTION,
                        importance=8.0,
                    )
                )
        return out

    return reasoner


def make_answer_agent(llm):
    from nemo_oo_agents import PredictStrategy
    from nemo_oo_agents.decorators import strategy

    class Answerer(Agent, llm=llm):
        @strategy(PredictStrategy())
        async def answer(self, question: str, memory_excerpts: str) -> str:
            """Answer using ONLY these memory excerpts, listing every relevant detail:

            ---
            {memory_excerpts}
            ---
            Question: {question}
            """
            ...

    return Answerer


def _completeness(answer: str, tokens: list[str]) -> float:
    a = (answer or "").lower()
    return sum(t in a for t in tokens) / len(tokens)


async def run_condition(*, reflect_on: bool, backend: str, embed: str, top_k: int) -> float:
    llm = build_llm()
    agent = make_answer_agent(llm)()
    reasoner = make_llm_reasoner(llm) if reflect_on else None
    manager = MemoryManager.install(
        agent,
        config=MemoryConfig(
            enabled=True,
            path=":memory:",
            vector=VectorConfig(backend=backend),
            embedding=build_embedding_config(embed),
            retrieval=RetrievalConfig(hops=0, top_k=top_k, n_dense=80, n_sparse=80),
            spontaneous=SpontaneousConfig(enabled=False),
            reflection=ReflectionPolicy(enabled=True, merge_threshold=0.97, edge_threshold=0.5),
        ),
        reasoner=reasoner,
    )

    # The agent's episodic experience log (many scattered single-fact episodes).
    for topic, facts in TOPICS.items():
        for sentence, _tok in facts:
            m = Memory(content=sentence, type=MemoryType.EPISODE, tags=[topic])
            manager.store.add(m, manager.embedder.embed(m.embedding_text()))
    log.info("recorded %d episodes", manager.store.count())

    if reflect_on:
        report = manager.reflect()  # consolidate: LLM reasoner abstracts episodes -> reflections
        log.info("reflect: %s | store now %d", report.model_dump(), manager.store.count())

    label = "reflect ON " if reflect_on else "reflect OFF"
    scores: list[float] = []
    for question, topic in QUESTIONS.items():
        hits = manager.recall(question, k=top_k)
        excerpts = "\n".join(m.content for m in hits) or "(nothing)"
        ans = await agent.answer(question, excerpts)
        tokens = [tok for _s, tok in TOPICS[topic]]
        c = _completeness(ans, tokens)
        scores.append(c)
        present = [tok for tok in tokens if tok in (ans or "").lower()]
        log.info("[%s] %-45s completeness=%.0f%% %s", label, topic, 100 * c, present)
    manager.uninstall()
    return sum(scores) / len(scores)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    p.add_argument(
        "--top-k", type=int, default=3, help="retrieval budget (small, so consolidation matters)"
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.verbose:
        logging.getLogger("nemo_oo_agents.memory").setLevel(logging.DEBUG)

    if not has_llm_creds():
        print(
            "reflecting.py needs an LLM (the reasoner + answerer are real). Set ARC_LLM_* (see .env).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    async def go():
        off = await run_condition(
            reflect_on=False, backend=args.backend, embed=args.embedder, top_k=args.top_k
        )
        on = await run_condition(
            reflect_on=True, backend=args.backend, embed=args.embedder, top_k=args.top_k
        )
        return off, on

    off, on = asyncio.run(go())
    print("\n" + "=" * 60)
    print("REFLECTION (consolidation) — synthesis completeness")
    print("=" * 60)
    print(f"  reflect OFF (raw episodes):   {off:.0%}")
    print(f"  reflect ON  (consolidated):   {on:.0%}")
    print(f"  --> gain from reflection:     {on - off:+.0%}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
