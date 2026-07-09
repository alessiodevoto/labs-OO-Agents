# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LoCoMo — a popular long-term conversational-memory benchmark.

LoCoMo (Maharana et al., 2024; https://github.com/snap-research/locomo) is the
benchmark memory systems (Mem0, Zep, …) report on: very long multi-session
dialogues (~19 sessions, ~300 turns) with hundreds of QA pairs that can only be
answered by recalling earlier turns.

This agent demonstrates the memory subsystem on it — and, crucially, the agent
**authors its own memories** (our differentiator): nothing extracts/stores them
for it.

1. **Memorize**: the agent reads each session and writes its own schema-structured
   memories with ``self.remember(...)`` (no raw bulk-store, no harness extraction).
2. For each question, **retrieve** the relevant memories (dense + sparse + ACT-R
   scoring) and answer — vs. an ablation with **no memory** at all.
3. Score with an LLM judge (gpt-5.4).

The headline number is the accuracy gain of memory ON over OFF. Because the agent
authors the memories, this benchmark **requires LLM credentials** (see ``llm.py`` /
``.env``); there is no offline/raw fallback.

Run::

    uv run python examples/memory_bench/locomo.py --backend chroma_embedded --embedder litellm --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402
from nooa_tui.memory import MemoryConfig, MemoryManager, MemoryToolsMixin  # noqa: E402
from nooa_tui.memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)

from nooa import Agent  # noqa: E402

log = logging.getLogger("locomo")

DATA_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DATA_PATH = Path(__file__).resolve().parent / "data" / "locomo10.json"

# LoCoMo answerable categories (5 = adversarial/abstention; excluded from the gain demo).
ANSWERABLE = (1, 2, 4)  # multi-hop, temporal, single-hop


@dataclass
class Turn:
    date: str
    speaker: str
    text: str

    def as_memory_text(self) -> str:
        return f"[{self.date}] {self.speaker}: {self.text}"


@dataclass
class QA:
    question: str
    answer: str
    category: int


def ensure_dataset(path: Path = DATA_PATH) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading LoCoMo to %s ...", path)
        urllib.request.urlretrieve(DATA_URL, path)  # noqa: S310
    return path


def load_sample(path: Path, index: int) -> tuple[list[Turn], list[QA]]:
    import json

    data = json.loads(path.read_text())
    sample = data[index]
    conv = sample["conversation"]
    turns: list[Turn] = []
    sess_ids = sorted(
        (k for k in conv if re.fullmatch(r"session_\d+", k)),
        key=lambda k: int(k.split("_")[1]),
    )
    for sid in sess_ids:
        date = conv.get(f"{sid}_date_time", "")
        for t in conv[sid]:
            txt = t.get("text") or t.get("blip_caption") or ""
            if txt:
                turns.append(Turn(date, t.get("speaker", "?"), txt))
    qa = [
        QA(str(q["question"]), str(q.get("answer", "")), int(q.get("category", 0)))
        for q in sample["qa"]
        if q.get("category") in ANSWERABLE and q.get("answer") not in (None, "")
    ]
    return turns, qa


CAT_NAME = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}


def select_balanced(qa: list[QA], limit: int) -> list[QA]:
    """Round-robin across categories so a small --limit isn't all one (hard) category."""
    from collections import defaultdict, deque

    by_cat: dict[int, deque] = defaultdict(deque)
    for q in qa:
        by_cat[q.category].append(q)
    queues = [by_cat[c] for c in sorted(by_cat)]
    out: list[QA] = []
    while len(out) < limit and any(queues):
        for dq in queues:
            if dq:
                out.append(dq.popleft())
                if len(out) >= limit:
                    break
    return out


def make_memory_agent(llm, memorize_max_iterations: int | None = None):
    """An agent that AUTHORS its own memories (memorize) and answers from them.

    ``memorize_max_iterations`` bounds the CodeAct memorize loop. The default (None) keeps
    the framework default (unbounded); pass an int to cap the number of tool-call rounds so
    a reasoning model that never emits ``return_result`` can't loop forever.
    """
    from nooa import PredictStrategy
    from nooa.config.strategy_config import CodeActConfig
    from nooa.decorators import strategy
    from nooa.strategies import CodeActStrategy

    memorize_deco = (
        strategy(CodeActStrategy(config=CodeActConfig(max_iterations=memorize_max_iterations)))
        if memorize_max_iterations is not None
        else (lambda f: f)
    )

    class LoCoMoAgent(MemoryToolsMixin, Agent, llm=llm):
        @memorize_deco
        async def memorize(self, session: str, date: str) -> str:
            """Update your long-term memory from this conversation session (dated {date}).

            Conversation:
            ---
            {session}
            ---
            Extract the durable, reusable facts, events, preferences and decisions and WRITE
            each as its own memory with self.remember(...), following your memory schema (choose
            a type, set importance, and tag the people / topics / dates involved). Skip greetings
            and small talk. Reply with the number of memories you wrote.

            {doc(self)}
            """
            ...

        @strategy(PredictStrategy())
        async def answer(self, question: str, memory_excerpts: str) -> str:
            """You are recalling your own past conversations to answer a question.

            Excerpts retrieved from your long-term memory:
            ---
            {memory_excerpts}
            ---
            Question: {question}

            Answer in as few words as possible using ONLY the excerpts above. If they
            do not contain the answer, reply exactly: No information available.
            """
            ...

    return LoCoMoAgent


def group_sessions(turns: list[Turn]) -> list[tuple[str, str]]:
    """Group consecutive same-date turns back into (date, transcript) sessions."""
    out: list[tuple[str, str]] = []
    cur: str | None = None
    buf: list[str] = []
    for t in turns:
        if cur is not None and t.date != cur and buf:
            out.append((cur, "\n".join(buf)))
            buf = []
        cur = t.date
        buf.append(f"{t.speaker}: {t.text}")
    if buf and cur is not None:
        out.append((cur, "\n".join(buf)))
    return out


def group_units(turns: list[Turn], max_sessions: int | None = None) -> list[tuple[str, list[str]]]:
    """Like group_sessions but keeps each turn separate: (date, [turn_text, ...])."""
    out: list[tuple[str, list[str]]] = []
    cur: str | None = None
    buf: list[str] = []
    for t in turns:
        if cur is not None and t.date != cur and buf:
            out.append((cur, buf))
            buf = []
        cur = t.date
        buf.append(f"{t.speaker}: {t.text}")
    if buf and cur is not None:
        out.append((cur, buf))
    return out[:max_sessions] if max_sessions else out


async def ingest_agentic(
    agent, manager: MemoryManager, turns: list[Turn], max_sessions: int | None
) -> None:
    """The AGENT reads each session and writes its OWN memories — the whole point.

    Unlike other systems (and unlike a raw bulk-store), nothing here extracts or
    stores memories for the agent: the agent calls self.remember(...) per the schema.
    """
    sessions = group_sessions(turns)
    if max_sessions:
        sessions = sessions[:max_sessions]
    for date, text in sessions:
        try:
            await agent.memorize(text, date)
        except Exception as e:  # noqa: BLE001
            log.warning("memorize failed for session %s: %r", date, e)
    log.info("agent authored %d memories from %d sessions", manager.store.count(), len(sessions))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def grade_substring(gold: str, pred: str) -> bool:
    g, p = _norm(gold), _norm(pred)
    if not g:
        return False
    if g in p:
        return True
    # date-ish / multi-token gold: count token overlap
    gt = [t for t in g.split() if len(t) > 2]
    return bool(gt) and sum(t in p for t in gt) / len(gt) >= 0.6


def grade_llm(judge_llm, question: str, gold: str, pred: str) -> bool:
    prompt = (
        "You grade a QA system. Reply with a single word: YES or NO.\n"
        f"Question: {question}\nGold answer: {gold}\nPredicted answer: {pred}\n"
        "Is the predicted answer correct (same meaning as the gold answer)?"
    )
    try:
        resp = judge_llm.call([{"role": "user", "content": prompt}])
        return "yes" in (resp.content or "").strip().lower()[:5]
    except Exception:
        return grade_substring(gold, pred)


async def qa_pass(
    agent, manager, qa, *, top_k: int, hops: int, judge_llm, label: str
) -> list[tuple[int, bool]]:
    """Answer every question; retrieve from `manager` (or no memory if None)."""
    results: list[tuple[int, bool]] = []
    for q in qa:
        if manager is not None:
            hits = manager.recall(q.question, k=top_k, hops=hops)
            excerpts = "\n".join(m.content for m in hits) or "(nothing retrieved)"
        else:
            excerpts = "(no memory available)"
        pred = await agent.answer(q.question, excerpts)
        ok = grade_llm(judge_llm, q.question, q.answer, pred)
        results.append((q.category, bool(ok)))
        log.info(
            "[%s][%s] %s | gold=%r -> %s",
            label,
            CAT_NAME.get(q.category, q.category),
            q.question[:52],
            q.answer[:30],
            "OK" if ok else "x",
        )
    return results


def _install_memory(agent, *, backend, embed, top_k, reflect: bool, reasoner):
    return MemoryManager.install(
        agent,
        config=MemoryConfig(
            enabled=True,
            path=":memory:",
            vector=VectorConfig(backend=backend),
            embedding=build_embedding_config(embed),
            retrieval=RetrievalConfig(hops=0, top_k=top_k, n_dense=80, n_sparse=80),
            spontaneous=SpontaneousConfig(enabled=False),  # explicit recall at QA time
            reflection=ReflectionPolicy(enabled=reflect, merge_threshold=0.95, edge_threshold=0.55),
        ),
        reasoner=reasoner,
    )


async def run_condition(
    *,
    memory_on: bool,
    turns,
    qa,
    backend: str,
    embed: str,
    top_k: int,
    judge_llm,
    max_sessions: int | None = None,
    write: str = "agent",
    window_k: int = 40,
    chunk_chars: int = 600,
) -> tuple[list[tuple[int, bool]], int]:
    from writers import write_memories

    llm = build_llm()
    agent = make_memory_agent(llm)()
    manager: MemoryManager | None = None
    stored = 0
    if memory_on:
        manager = _install_memory(
            agent, backend=backend, embed=embed, top_k=top_k, reflect=False, reasoner=None
        )
        if write == "agent":
            await ingest_agentic(
                agent, manager, turns, max_sessions
            )  # agent writes its own memories
        else:
            units = group_units(turns, max_sessions)
            write_memories(
                manager, units, strategy=write, k=window_k, chunk_chars=chunk_chars, llm=llm
            )
        stored = manager.store.count()
        log.info("[%s] stored=%d", write, stored)
    results = await qa_pass(
        agent,
        manager,
        qa,
        top_k=top_k,
        hops=0,
        judge_llm=judge_llm,
        label=("ON/" + write) if memory_on else "OFF",
    )
    if manager is not None:
        manager.uninstall()
    return results, stored


async def run_reflect_ablation(
    *,
    turns,
    qa,
    backend: str,
    embed: str,
    top_k: int,
    judge_llm,
    max_sessions: int | None,
):
    """A/B reflection on the SAME agent-authored memories: QA before vs after reflect()."""
    from reflecting import make_llm_reasoner  # reuse the LLM abstraction reasoner

    llm = build_llm()
    agent = make_memory_agent(llm)()
    manager = _install_memory(
        agent,
        backend=backend,
        embed=embed,
        top_k=top_k,
        reflect=True,
        reasoner=make_llm_reasoner(llm),
    )
    await ingest_agentic(agent, manager, turns, max_sessions)  # author once
    n_before = manager.store.count()

    pre = await qa_pass(
        agent, manager, qa, top_k=top_k, hops=0, judge_llm=judge_llm, label="ON       "
    )
    report = manager.reflect()  # consolidate: merge + edges + re-score + LLM abstraction
    log.info(
        "reflect: %s | memories %d -> %d", report.model_dump(), n_before, manager.store.count()
    )
    post = await qa_pass(
        agent, manager, qa, top_k=top_k, hops=1, judge_llm=judge_llm, label="ON+reflect "
    )
    manager.uninstall()

    # no-memory reference
    off = await qa_pass(
        make_memory_agent(llm)(),
        None,
        qa,
        top_k=top_k,
        hops=0,
        judge_llm=judge_llm,
        label="OFF      ",
    )
    return off, pre, post, report


def _overall(res):
    return sum(ok for _, ok in res), len(res)


def _by_cat(res):
    d: dict[int, list[int]] = {}
    for c, ok in res:
        d.setdefault(c, [0, 0])
        d[c][0] += ok
        d[c][1] += 1
    return d


def _report_reflect(off, pre, post, report) -> None:
    print("\n" + "=" * 72)
    print("LoCoMo + REFLECT — does consolidation help? (same agent-authored memories)")
    print("=" * 72)
    oc, pc, qc = _by_cat(off), _by_cat(pre), _by_cat(post)
    cats = sorted(set(oc) | set(pc) | set(qc))
    print(f"  {'category':12s} {'OFF':>11s} {'ON':>11s} {'ON+reflect':>11s}")

    def cell(d, c):
        x = d.get(c)
        return f"{x[0]}/{x[1]} ({x[0] / x[1]:.0%})" if x else "-"

    for c in cats:
        print(
            f"  {CAT_NAME.get(c, str(c)):12s} {cell(oc, c):>11s} {cell(pc, c):>11s} {cell(qc, c):>11s}"
        )
    print("  " + "-" * 48)
    for label, res in [("OFF", off), ("ON", pre), ("ON+reflect", post)]:
        cc, tt = _overall(res)
        print(f"  {label:12s} {f'{cc}/{tt} ({cc / max(1, tt):.0%})':>11s}")
    pcn, ptn = _overall(pre)
    qcn, qtn = _overall(post)
    print(f"  --> reflect effect (ON+reflect − ON): {qcn / max(1, qtn) - pcn / max(1, ptn):+.0%}")
    print(f"  consolidation: {report.model_dump()}")
    print("=" * 72 + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    p.add_argument("--sample", type=int, default=0, help="which LoCoMo conversation (0-9)")
    p.add_argument("--limit", type=int, default=20, help="max questions")
    p.add_argument("--top-k", type=int, default=15, help="turns retrieved per question")
    p.add_argument("--max-sessions", type=int, default=None, help="cap sessions ingested")
    p.add_argument(
        "--write",
        nargs="+",
        default=["agent"],
        help="write-op strategies to compare: agent raw window chunk llm-summary",
    )
    p.add_argument("--window-k", type=int, default=40)
    p.add_argument("--chunk-chars", type=int, default=600)
    p.add_argument(
        "--reflect",
        action="store_true",
        help="A/B reflection: QA before vs after manager.reflect() on the same memories",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.verbose:
        logging.getLogger("nooa.memory").setLevel(logging.DEBUG)

    # The agent authors its own memories — there is no raw/harness fallback — so this
    # benchmark requires an LLM. No credentials => stop with a clear message.
    if not has_llm_creds():
        print(
            "LoCoMo here is AGENT-AUTHORED: the agent writes its own memories, so it needs an "
            "LLM. Set ARC_LLM_MODEL / ARC_LLM_BASE_URL / ARC_LLM_API_KEY (see llm.py / .env).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    turns, qa = load_sample(ensure_dataset(), args.sample)
    qa = select_balanced(qa, args.limit)
    print(
        f"backend={args.backend} embedder={args.embedder} ingest=agent "
        f"sample={args.sample} turns={len(turns)} questions={len(qa)}\n"
    )

    judge_llm = build_llm()

    if args.reflect:
        off, pre, post, report = asyncio.run(
            run_reflect_ablation(
                turns=turns,
                qa=qa,
                backend=args.backend,
                embed=args.embedder,
                top_k=args.top_k,
                judge_llm=judge_llm,
                max_sessions=args.max_sessions,
            )
        )
        _report_reflect(off, pre, post, report)
        return

    common = {
        "turns": turns,
        "qa": qa,
        "backend": args.backend,
        "embed": args.embedder,
        "top_k": args.top_k,
        "judge_llm": judge_llm,
        "max_sessions": args.max_sessions,
        "window_k": args.window_k,
        "chunk_chars": args.chunk_chars,
    }

    async def go():
        off, _ = await run_condition(memory_on=False, **common)
        rows = [("OFF", off, 0)]
        for w in args.write:
            res, stored = await run_condition(memory_on=True, write=w, **common)
            rows.append((w, res, stored))
        return rows

    rows = asyncio.run(go())

    print("\n" + "=" * 78)
    print("LoCoMo — write-op ablation (retrieval + answer fixed; only the write op varies)")
    print("=" * 78)
    cats = sorted({c for _, res, _ in rows for c, _ in res})
    header = (
        f"  {'write':12s} {'overall':>11s} "
        + " ".join(f"{CAT_NAME.get(c, str(c)):>11s}" for c in cats)
        + f" {'stored':>7s}"
    )
    print(header)
    for label, res, stored in rows:
        oc, ot = _overall(res)
        bc = _by_cat(res)
        cells = " ".join(
            (f"{bc[c][0]}/{bc[c][1]} ({bc[c][0] / bc[c][1]:.0%})" if c in bc else "-").rjust(11)
            for c in cats
        )
        print(f"  {label:12s} {f'{oc}/{ot} ({oc / max(1, ot):.0%})':>11s} {cells} {stored:>7d}")
    # agent vs best deterministic
    det = [(w, _overall(r)) for w, r, _ in rows if w not in ("OFF", "agent")]
    ag = next((_overall(r) for w, r, _ in rows if w == "agent"), None)
    if ag and det:
        best_w, (bc, bt) = max(det, key=lambda x: x[1][0] / max(1, x[1][1]))
        print(
            f"  --> agent {ag[0] / max(1, ag[1]):.0%} vs best deterministic ({best_w}) "
            f"{bc / max(1, bt):.0%}: {ag[0] / max(1, ag[1]) - bc / max(1, bt):+.0%}"
        )
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
