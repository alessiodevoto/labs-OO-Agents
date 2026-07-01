# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LongMemEval — where *reflection* (consolidation + reconsolidation) should help.

LongMemEval (Wu et al., 2025; https://github.com/xiaowu0162/LongMemEval) is a
popular long-term-memory benchmark. Two of its categories reward consolidation —
the opposite of LoCoMo's pinpoint lookups:

* ``multi-session``    — the answer is spread across sessions and must be aggregated.
* ``knowledge-update`` — a fact changes over time; you must use the *latest* value.

Each question has its own (oracle) haystack of sessions. The agent **authors its
own memories** from them, then we compare three conditions:

* **OFF**        — no memory.
* **ON**         — agent-authored memories, plain retrieval.
* **ON+reflect** — after ``manager.reflect()``: an LLM **reasoner** consolidates scattered
  facts into reflections, and an LLM **reconciler** resolves outdated values
  (keep-latest) — then retrieve (with a graph hop) and answer.

Requires LLM credentials. The dataset auto-downloads (HF longmemeval-cleaned, oracle
split; not vendored). Run::

    uv run python examples/memory_bench/longmemeval.py --backend chroma_embedded --embedder litellm --per-cat 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402
from locomo import grade_llm, make_memory_agent  # noqa: E402
from reflecting import make_llm_reasoner  # noqa: E402

from nemo_oo_agents.memory import MemoryConfig, MemoryManager  # noqa: E402
from nemo_oo_agents.memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)
from nemo_oo_agents.memory.schema import Memory, MemoryType  # noqa: E402

log = logging.getLogger("longmemeval")

DATA_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json"
DATA_PATH = Path(__file__).resolve().parent / "data" / "lme_oracle.json"
CONSOLIDATION_CATEGORIES = ("knowledge-update", "multi-session")


def ensure_dataset(path: Path = DATA_PATH) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading LongMemEval (oracle) to %s ...", path)
        urllib.request.urlretrieve(DATA_URL, path)  # noqa: S310
    return path


def make_llm_reconciler(llm):
    """LLM reconsolidation: resolve outdated values in a cluster, keep the current one."""

    def reconciler(cluster: list[Memory]):
        lines = [f"[{i}] {m.content}" for i, m in enumerate(cluster)]  # oldest -> newest
        prompt = (
            "These memories may describe the SAME thing at different times (listed oldest "
            "first), possibly with OUTDATED values. If some are outdated, write ONE consolidated "
            "note stating the CURRENT/most-recent value, and list the outdated indices. If they "
            "are unrelated or already consistent, return empty.\n"
            'Respond ONLY as JSON: {"current": "<note or empty>", "outdated": [<indices>]}\n'
            "Memories:\n" + "\n".join(lines)
        )
        try:
            txt = llm.call([{"role": "user", "content": prompt}]).content or ""
            data = json.loads(txt[txt.find("{") : txt.rfind("}") + 1])
        except Exception:  # noqa: BLE001
            return None, []
        idxs = [
            i for i in (data.get("outdated") or []) if isinstance(i, int) and 0 <= i < len(cluster)
        ]
        if not idxs:
            return None, []
        cur = (data.get("current") or "").strip()
        consolidated = Memory(content=cur, type=MemoryType.INFO, importance=8.0) if cur else None
        return consolidated, [cluster[i].id for i in idxs]

    return reconciler


def _session_text(session: list[dict]) -> str:
    return "\n".join(f"{t.get('role', '?')}: {t.get('content', '')}" for t in session)


async def run_question(item, *, backend, embed, top_k, judge_llm, max_sessions):
    q, gold, cat = item["question"], str(item["answer"]), item["question_type"]
    sessions = item["haystack_sessions"][:max_sessions]
    dates = (item.get("haystack_dates") or [""] * len(sessions))[:max_sessions]

    # OFF — no memory at all
    off_agent = make_memory_agent(build_llm())()
    off_ok = grade_llm(judge_llm, q, gold, await off_agent.answer(q, "(no memory available)"))

    # ON and ON+reflect share the SAME agent-authored memories
    llm = build_llm()
    agent = make_memory_agent(llm)()
    mgr = MemoryManager.install(
        agent,
        config=MemoryConfig(
            enabled=True,
            path=":memory:",
            vector=VectorConfig(backend=backend),
            embedding=build_embedding_config(embed),
            retrieval=RetrievalConfig(hops=0, top_k=top_k, n_dense=60, n_sparse=60),
            spontaneous=SpontaneousConfig(enabled=False),
            reflection=ReflectionPolicy(
                enabled=True, merge_threshold=0.95, edge_threshold=0.6, recon_threshold=0.55
            ),
        ),
        reasoner=make_llm_reasoner(llm),
        reconciler=make_llm_reconciler(llm),
    )
    for sess, date in zip(sessions, dates, strict=False):
        try:
            await agent.memorize(_session_text(sess), date)
        except Exception as e:  # noqa: BLE001
            log.warning("memorize failed: %r", e)

    on_hits = mgr.recall(q, k=top_k, hops=0)
    on_ok = grade_llm(
        judge_llm, q, gold, await agent.answer(q, "\n".join(m.content for m in on_hits) or "(none)")
    )

    report = mgr.reflect()
    ond_hits = mgr.recall(q, k=top_k, hops=1)
    ond_ok = grade_llm(
        judge_llm,
        q,
        gold,
        await agent.answer(q, "\n".join(m.content for m in ond_hits) or "(none)"),
    )
    mgr.uninstall()

    log.info(
        "[%s] OFF=%d ON=%d ON+reflect=%d | reflect=%s | %s",
        cat,
        off_ok,
        on_ok,
        ond_ok,
        {k: v for k, v in report.model_dump().items() if v},
        q[:50],
    )
    return cat, off_ok, on_ok, ond_ok


async def run_question_writes(
    item, *, writes, backend, embed, top_k, judge_llm, max_sessions, window_k, chunk_chars
):
    """Write-op ablation per question: OFF + one ON per write strategy (no reflect)."""
    from writers import write_memories

    q, gold, cat = item["question"], str(item["answer"]), item["question_type"]
    sessions = item["haystack_sessions"][:max_sessions]
    dates = (item.get("haystack_dates") or [""] * len(sessions))[:max_sessions]
    units = [
        (d, [f"{t.get('role', '?')}: {t.get('content', '')}" for t in sess])
        for sess, d in zip(sessions, dates, strict=False)
    ]

    off_agent = make_memory_agent(build_llm())()
    res = {"OFF": grade_llm(judge_llm, q, gold, await off_agent.answer(q, "(no memory available)"))}
    stored = {}
    for w in writes:
        llm = build_llm()
        agent = make_memory_agent(llm)()
        mgr = MemoryManager.install(
            agent,
            config=MemoryConfig(
                enabled=True,
                path=":memory:",
                vector=VectorConfig(backend=backend),
                embedding=build_embedding_config(embed),
                retrieval=RetrievalConfig(hops=0, top_k=top_k, n_dense=60, n_sparse=60),
                spontaneous=SpontaneousConfig(enabled=False),
                reflection=ReflectionPolicy(enabled=False),
            ),
        )
        if w == "agent":
            for sess, date in zip(sessions, dates, strict=False):
                try:
                    await agent.memorize(_session_text(sess), date)
                except Exception as e:  # noqa: BLE001
                    log.warning("memorize failed: %r", e)
        else:
            write_memories(mgr, units, strategy=w, k=window_k, chunk_chars=chunk_chars, llm=llm)
        hits = mgr.recall(q, k=top_k, hops=0)
        res[w] = grade_llm(
            judge_llm,
            q,
            gold,
            await agent.answer(q, "\n".join(m.content for m in hits) or "(none)"),
        )
        stored[w] = mgr.store.count()
        mgr.uninstall()
    log.info("[%s] %s | %s", cat, " ".join(f"{k}={int(v)}" for k, v in res.items()), q[:46])
    return cat, res, stored


def _report_writes(results, writes, categories) -> None:
    labels = ["OFF", *writes]
    cats = [c for c in categories if any(cat == c for cat, _r, _s in results)]
    agg = {lab: {c: [0, 0] for c in cats} for lab in labels}
    tot = {lab: [0, 0] for lab in labels}
    stored_tot = dict.fromkeys(writes, 0)
    for cat, res, stored in results:
        for lab in labels:
            v = int(res.get(lab, 0))
            agg[lab][cat][0] += v
            agg[lab][cat][1] += 1
            tot[lab][0] += v
            tot[lab][1] += 1
        for w in writes:
            stored_tot[w] += stored.get(w, 0)

    def pct(cell):
        c, n = cell
        return f"{c}/{n} ({c / n:.0%})" if n else "-"

    print("\n" + "=" * 78)
    print("LongMemEval — write-op ablation (retrieval + answer fixed; only the write op varies)")
    print("=" * 78)
    print(f"  {'write':12s} {'overall':>12s} " + " ".join(f"{c:>16s}" for c in cats))
    for lab in labels:
        cells = " ".join(pct(agg[lab][c]).rjust(16) for c in cats)
        print(f"  {lab:12s} {pct(tot[lab]):>12s} {cells}")
    ag = tot.get("agent")
    det = [(w, tot[w]) for w in writes if w != "agent"]
    if ag and det:
        bw, bt = max(det, key=lambda x: x[1][0] / max(1, x[1][1]))
        print(
            f"  --> agent {ag[0] / max(1, ag[1]):.0%} vs best deterministic ({bw}) "
            f"{bt[0] / max(1, bt[1]):.0%}: {ag[0] / max(1, ag[1]) - bt[0] / max(1, bt[1]):+.0%}"
        )
    print(
        "  stored memories (avg/q): "
        + ", ".join(f"{w}={stored_tot[w] / max(1, len(results)):.0f}" for w in writes)
    )
    print("=" * 78 + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    p.add_argument("--per-cat", type=int, default=4, help="questions per category")
    p.add_argument("--categories", nargs="+", default=list(CONSOLIDATION_CATEGORIES))
    p.add_argument("--top-k", type=int, default=8)
    p.add_argument("--max-sessions", type=int, default=10)
    p.add_argument(
        "--write",
        nargs="+",
        default=None,
        help="write-op ablation (OFF + one ON per strategy, no reflect): agent raw window chunk llm-summary",
    )
    p.add_argument("--window-k", type=int, default=40)
    p.add_argument("--chunk-chars", type=int, default=600)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.verbose:
        logging.getLogger("nemo_oo_agents.memory").setLevel(logging.DEBUG)

    if not has_llm_creds():
        print(
            "longmemeval.py needs an LLM (agent authors memories; reasoner+reconciler are real). "
            "Set ARC_LLM_* (see .env).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    data = json.loads(ensure_dataset().read_text())
    by_cat: dict[str, list] = {}
    for it in data:
        c = it.get("question_type")
        if c in args.categories:
            by_cat.setdefault(c, []).append(it)
    items = [it for c in args.categories for it in by_cat.get(c, [])[: args.per_cat]]
    print(
        f"backend={args.backend} embedder={args.embedder} categories={args.categories} "
        f"questions={len(items)}\n"
    )

    judge_llm = build_llm()

    if args.write:
        writes = list(args.write)

        async def go_w():
            out = []
            for it in items:
                out.append(
                    await run_question_writes(
                        it,
                        writes=writes,
                        backend=args.backend,
                        embed=args.embedder,
                        top_k=args.top_k,
                        judge_llm=judge_llm,
                        max_sessions=args.max_sessions,
                        window_k=args.window_k,
                        chunk_chars=args.chunk_chars,
                    )
                )
            return out

        _report_writes(asyncio.run(go_w()), writes, args.categories)
        return

    async def go():
        out = []
        for it in items:
            out.append(
                await run_question(
                    it,
                    backend=args.backend,
                    embed=args.embedder,
                    top_k=args.top_k,
                    judge_llm=judge_llm,
                    max_sessions=args.max_sessions,
                )
            )
        return out

    results = asyncio.run(go())

    # aggregate per category + overall
    agg: dict[str, list[int]] = {}
    tot = [0, 0, 0, 0]
    for cat, off, on, ond in results:
        a = agg.setdefault(cat, [0, 0, 0, 0])
        for i, v in enumerate((off, on, ond, 1)):
            a[i] += v
            tot[i] += v

    print("\n" + "=" * 64)
    print("LongMemEval — does reflection (consolidate + reconsolidate) help?")
    print("=" * 64)
    print(f"  {'category':18s} {'OFF':>9s} {'ON':>9s} {'ON+reflect':>9s}")

    def pct(c, n):
        return f"{c}/{n} ({c / n:.0%})" if n else "-"

    for cat in args.categories:
        if cat in agg:
            off, on, ond, n = agg[cat]
            print(f"  {cat:18s} {pct(off, n):>9s} {pct(on, n):>9s} {pct(ond, n):>9s}")
    print("  " + "-" * 46)
    off, on, ond, n = tot
    print(f"  {'OVERALL':18s} {pct(off, n):>9s} {pct(on, n):>9s} {pct(ond, n):>9s}")
    print(f"  --> reflection effect (ON+reflect − ON): {ond / max(1, n) - on / max(1, n):+.0%}")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
