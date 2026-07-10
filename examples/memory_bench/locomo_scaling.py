# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LoCoMo scaling study — does the agent's WRITE op help as we cover more of the benchmark?

Unlike ``locomo.py`` (one conversation), this spans **all 10 LoCoMo conversations**
and ramps the evaluated question set as a *fraction of the full answerable QA set*
(cats 1/2/4): 5% -> 10% -> 20% -> 100%. At each subset it reports two arms. The **READ
op is held fixed** — both arms answer from the SAME recall(top-k) retrieval, and the
model NEVER sees the full conversation directly — so **only the WRITE op differs**:

* **BASE** = *write* — the agent authors its own curated memories (``self.remember(...)``);
  recall retrieves from those. This is the reference.
* **CONTROL** = *no write* — the raw conversation is stored verbatim (every turn), with no
  agent curation; recall retrieves from that. (This is the ``raw`` strategy in writers.py.)

So the question is precisely: *does the agent's curated writing beat just storing the raw
conversation, when the read op is identical?* Reflection is OFF for both (we never run
reflect-without-write).

The subsets are **nested prefixes** of a per-conversation, category-balanced ordering,
so 5% ⊂ 10% ⊂ 20% ⊂ 100% and each question is answered exactly once across the ramp
(answered in disjoint bands, reported cumulatively). Both stores are built once per
conversation up front and reused at every subset.

Single model throughout (gen + judge), embeddings via text-embedding-3-large::

    ARC_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra \
      uv run python examples/memory_bench/locomo_scaling.py \
      --backend chroma_embedded --embedder litellm --percents 5 10 20 100
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import datetime
import json
import logging
import math
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import locomo as L  # noqa: E402
from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402
from nooa_memory import MemoryConfig, MemoryManager, MemoryToolsMixin  # noqa: E402
from nooa_memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)

from nooa import Agent  # noqa: E402
from nooa.config.strategy_config import CodeActConfig  # noqa: E402
from nooa.decorators import strategy  # noqa: E402
from nooa.strategies import CodeActStrategy  # noqa: E402

log = logging.getLogger("locomo_scaling")

# Per-operation watchdogs: a single CodeAct session (memorize or agentic answer) can hang
# on a looping model; abandon it rather than stall the whole ramp.
MEMORIZE_TIMEOUT = 240.0  # seconds per session
ANSWER_TIMEOUT = 180.0  # seconds per question (agentic answer = several tool-call rounds)
ANSWER_MAX_ROUNDS = 8  # cap the agentic answer's CodeAct tool-call rounds


# ── Full-visibility logging ──────────────────────────────────────────────────
# Everything that happens — memories, spontaneous-injected context, the agent's
# reasoning, its recall/search tool calls + results, and the final answer — is
# written to <project root>/results/<timestamp>_locomo_scaling/ (gitignored).
#
# Per-question trajectory is captured by instrumenting the reader LLM's acall: every
# round's input messages (which carry the spontaneous block + prior tool results),
# the reasoning, the tool calls, and the content all pass through it. A contextvar
# routes each (concurrent) call's record to the right question's buffer.
_TRACE: contextvars.ContextVar = contextvars.ContextVar("trace", default=None)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _instrument_llm(client):
    """Wrap client.acall so each LLM round is appended to the current question's trace."""
    orig_acall = client.acall

    async def acall(*a, **k):
        resp = await orig_acall(*a, **k)
        buf = _TRACE.get()
        if buf is not None:
            messages = a[0] if a else k.get("messages")
            try:
                tcs = [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in (getattr(resp, "tool_calls", None) or [])
                ]
            except Exception:  # noqa: BLE001
                tcs = []
            content = getattr(resp, "content", None)
            buf.append(
                {
                    "input": _msgs_brief(messages),
                    "reasoning": (getattr(resp, "reasoning", None) or None),
                    "tool_calls": tcs,
                    "content": content if isinstance(content, str) else str(content),
                }
            )
        return resp

    client.acall = acall
    return client


def _msgs_brief(messages, cap: int = 4000):
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        c = c if isinstance(c, str) else json.dumps(c, default=str)
        rec = {"role": m.get("role"), "content": (c or "")[:cap]}
        if m.get("tool_calls"):
            rec["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            rec["tool_call_id"] = m["tool_call_id"]
        out.append(rec)
    return out


def _fsync(f) -> None:
    """Force a file's buffered writes all the way to disk (not just Python/OS RAM buffers)."""
    f.flush()
    os.fsync(f.fileno())


def _durable_write(path: Path, text: str) -> None:
    with open(path, "w") as f:
        f.write(text)
        _fsync(f)


class RunLogger:
    """Per-run artifact dir: config, memory dumps, per-question trajectories, reports.

    Hierarchy: <root>/results/locomo_scaling/<timestamp>/ . Every write is flushed AND
    fsync'd to disk immediately (nothing left cached in RAM), so a crash/restart loses
    nothing already produced.
    """

    def __init__(self, config: dict, stamp: str):
        self.dir = PROJECT_ROOT / "results" / "locomo_scaling" / stamp
        (self.dir / "memories").mkdir(parents=True, exist_ok=True)
        self.stores_dir = self.dir / "stores"
        self.stores_dir.mkdir(parents=True, exist_ok=True)
        _durable_write(self.dir / "config.json", json.dumps(config, indent=2))
        self._traj = open(self.dir / "trajectories.jsonl", "w")  # noqa: SIM115
        self._reports = open(self.dir / "reports.txt", "w")  # noqa: SIM115
        log.info("logging full run artifacts to %s", self.dir)

    def dump_memories(self, idx: int, arm: str, store) -> None:
        path = self.dir / "memories" / f"conv{idx}_{arm}.jsonl"
        with open(path, "w") as f:
            for m in store.all_memories():
                f.write(
                    json.dumps(
                        {
                            "id": m.id,
                            "type": m.type.value,
                            "content": m.content,
                            "importance": m.importance,
                            "created_at": m.created_at,
                        },
                        default=str,
                    )
                    + "\n"
                )
            _fsync(f)

    def log_qa(self, rec: dict) -> None:
        self._traj.write(json.dumps(rec, default=str) + "\n")
        _fsync(self._traj)

    def log_report(self, text: str) -> None:
        self._reports.write(text + "\n")
        _fsync(self._reports)

    def write_summary(self, summary: dict) -> None:
        _durable_write(self.dir / "summary.json", json.dumps(summary, indent=2, default=str))

    def close(self) -> None:
        self._traj.close()
        self._reports.close()


def make_reader_agent(llm):
    """A reader agent that answers AGENTICALLY using the memory READ ops.

    The read pipeline is the memory system's own: (1) spontaneous recall auto-injects the
    most relevant memories into context each turn, then (2) the agent can deliberately call
    self.recall(query, k) (associative) and self.search(query, k) (keyword/term) to dig
    further. It never sees the full conversation — only what the read ops surface.
    """

    class ReaderAgent(MemoryToolsMixin, Agent, llm=llm):
        @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=ANSWER_MAX_ROUNDS)))
        async def answer(self, question: str) -> str:
            """Answer the question from YOUR MEMORY only.

            Relevant memories may already be shown to you above (spontaneous recall). You can
            ALSO deliberately retrieve more before answering:
              - self.recall(query, k)  → associative recall (similarity + graph)
              - self.search(query, k)  → keyword/term lookup (deliberate)
            Retrieve what you need, then answer in as FEW words as possible using only your
            memories. If they do not contain the answer, reply exactly: No information available.

            Question: {question}

            {doc(self)}
            """
            ...

    return ReaderAgent


def _count_memories(path: str) -> int:
    """Count non-archived memories in a store file (raw sqlite, no index rebuild needed)."""
    import sqlite3

    con = sqlite3.connect(path)
    try:
        return int(con.execute("SELECT COUNT(*) FROM memories WHERE archived=0").fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0
    finally:
        con.close()


def _checkpoint_store(store) -> None:
    """Merge the SQLite WAL into the main DB file so the data is durably on disk, not cached.

    The store uses WAL mode; without this, freshly-written memories sit in the .sqlite-wal
    sidecar (effectively RAM/OS-buffered) until a checkpoint, so a copy of the .sqlite alone
    would be near-empty. TRUNCATE also removes the WAL afterwards.
    """
    try:
        store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store._conn.commit()
    except Exception:  # noqa: BLE001
        log.debug("wal_checkpoint failed", exc_info=True)


def _install_store(agent, *, path: str, top_k: int, spontaneous: bool, embed: str) -> MemoryManager:
    """Install a memory manager backed by a numpy file store (persists + reopens cleanly)."""
    return MemoryManager.install(
        agent,
        config=MemoryConfig(
            enabled=True,
            path=path,
            vector=VectorConfig(backend="numpy"),
            embedding=build_embedding_config(embed),
            retrieval=RetrievalConfig(hops=0, top_k=top_k, n_dense=80, n_sparse=80),
            spontaneous=SpontaneousConfig(enabled=spontaneous, top_k=top_k),
            reflection=ReflectionPolicy(enabled=False),
        ),
    )


def balanced_order(qa: list[L.QA]) -> list[L.QA]:
    """Round-robin across categories (returns ALL questions, just reordered).

    Taking a prefix of this ordering yields a category-balanced subset.
    """
    by_cat: dict[int, deque] = defaultdict(deque)
    for q in qa:
        by_cat[q.category].append(q)
    queues = [by_cat[c] for c in sorted(by_cat)]
    out: list[L.QA] = []
    while any(queues):
        for dq in queues:
            if dq:
                out.append(dq.popleft())
    return out


@dataclass
class ConvState:
    idx: int
    qa_ordered: list[L.QA]
    base_path: str  # WRITE = agent-authored memories (numpy file store)
    control_path: str  # WRITE = raw conversation, every turn stored verbatim (numpy file store)
    stored_base: int
    stored_control: int


async def _grade(judge_llm, q: L.QA, pred: str) -> bool:
    return bool(await asyncio.to_thread(L.grade_llm, judge_llm, q.question, q.answer, pred))


async def answer_and_grade(
    q: L.QA,
    store_path: str,
    *,
    top_k: int,
    embed: str,
    reader_cls,
    judge_llm,
    logger: RunLogger | None = None,
    conv_idx: int | None = None,
    arm: str | None = None,
) -> tuple[int, bool]:
    # A FRESH reader agent per question (no accumulated history) opens the arm's store and
    # answers AGENTICALLY: spontaneous recall auto-injects relevant memories, and the agent
    # may follow up with self.recall/self.search. Identical read pipeline for both arms; only
    # the store contents differ (agent-authored vs raw). The model never sees the full chat.
    agent = reader_cls()
    mgr = _install_store(agent, path=store_path, top_k=top_k, spontaneous=True, embed=embed)
    buf: list = []  # captures every LLM round (reasoning/tool calls/content)
    token = _TRACE.set(buf)
    pred: str | None = None
    err: str | None = None
    try:
        pred = await asyncio.wait_for(agent.answer(q.question), timeout=ANSWER_TIMEOUT)
    except Exception as e:  # noqa: BLE001  (timeout or error: don't let one question kill the ramp)
        err = str(e)[:200]
        log.warning("answer failed (cat %s): %r", q.category, err)
    finally:
        _TRACE.reset(token)
        mgr.uninstall()
        mgr.store.close()
    ok = await _grade(judge_llm, q, pred) if pred is not None else False
    if logger is not None:
        logger.log_qa(
            {
                "conv": conv_idx,
                "arm": arm,
                "category": q.category,
                "cat_name": L.CAT_NAME.get(q.category, str(q.category)),
                "question": q.question,
                "gold": q.answer,
                "predicted": pred,
                "correct": bool(ok),
                "error": err,
                "rounds": len(buf),
                "trace": buf,
            }
        )
    return q.category, bool(ok)


async def build_conv(
    idx: int,
    turns: list[L.Turn],
    *,
    embed: str,
    top_k: int,
    max_sessions: int | None,
    sem: asyncio.Semaphore,
    max_rounds: int | None,
    workdir: str,
    logger: RunLogger | None = None,
) -> ConvState:
    """Build BOTH stores for one conversation (numpy file stores), differing only in WRITE:

    * BASE store    — the agent authors its own memories (``self.remember(...)``).
    * CONTROL store — the raw conversation is stored verbatim (every turn), no curation.

    Both are later read with the IDENTICAL agentic read pipeline (spontaneous + search/recall).
    """
    from writers import write_memories

    llm = build_llm()
    base_path = os.path.join(workdir, f"base_{idx}.sqlite")
    control_path = os.path.join(workdir, f"control_{idx}.sqlite")
    sessions = L.group_sessions(turns)
    if max_sessions:
        sessions = sessions[:max_sessions]

    # --- BASE: agent authors curated memories, ONE FRESH AGENT PER SESSION ---
    # A reused agent accumulates CodeAct history across a conversation's sessions, so by the
    # 30th session it re-processes ~100k tokens per memorize call (pathologically slow + costly
    # on long convs). A fresh agent per session sees only that session (~1-2k tokens); the
    # memories still land in the shared on-disk store.
    mem_cls = L.make_memory_agent(llm, memorize_max_iterations=max_rounds)
    for date, text in sessions:
        async with sem:
            agent = mem_cls()
            mgr = _install_store(agent, path=base_path, top_k=top_k, spontaneous=False, embed=embed)
            try:
                await asyncio.wait_for(agent.memorize(text, date), timeout=MEMORIZE_TIMEOUT)
            except Exception as e:  # noqa: BLE001  (timeout or error: skip this session, keep going)
                log.warning("conv %d: memorize failed for %s: %r", idx, date, str(e)[:160])
            finally:
                _checkpoint_store(mgr.store)  # WAL -> main file each session: durable, not cached
                mgr.uninstall()
                mgr.store.close()
    # reopen once to count + dump the authored memories
    base_host = mem_cls()
    base_mgr = _install_store(
        base_host, path=base_path, top_k=top_k, spontaneous=False, embed=embed
    )
    stored_base = base_mgr.store.count()
    if logger is not None:
        logger.dump_memories(idx, "base", base_mgr.store)
    base_mgr.uninstall()
    base_mgr.store.close()

    # --- CONTROL: store the raw conversation (no agent, no curation) ---
    control_host = L.make_memory_agent(llm)()  # just hosts the manager; never generates
    control_mgr = _install_store(
        control_host, path=control_path, top_k=top_k, spontaneous=False, embed=embed
    )
    write_memories(
        control_mgr, L.group_units(turns, max_sessions), strategy="raw"
    )  # embeddings only
    stored_control = control_mgr.store.count()
    if logger is not None:
        logger.dump_memories(idx, "control", control_mgr.store)
    _checkpoint_store(control_mgr.store)
    control_mgr.uninstall()
    control_mgr.store.close()

    log.info(
        "conv %d: BASE(agent)=%d  CONTROL(raw)=%d memories  [%d sessions]",
        idx,
        stored_base,
        stored_control,
        len(sessions),
    )
    return ConvState(idx, [], base_path, control_path, stored_base, stored_control)


async def run_band(
    conv_states: list[ConvState],
    lo: float,
    hi: float,
    *,
    base: bool,
    top_k: int,
    judge_llm,
    sem: asyncio.Semaphore,
    reader_cls,
    embed: str,
    logger: RunLogger | None = None,
) -> list[tuple[int, bool]]:
    """Answer the disjoint band (lo, hi] (fractions) of each conversation's QA.

    A FRESH reader agent is built per question (inside answer_and_grade) so no per-instance
    history accumulates. The READ pipeline is identical across arms (spontaneous + agentic
    search/recall); only the store differs: agent-authored (BASE) vs raw conversation (CONTROL).
    """

    async def one_conv(cs: ConvState) -> list[tuple[int, bool]]:
        n = len(cs.qa_ordered)
        a = math.ceil(n * lo)
        b = n if hi >= 1.0 else math.ceil(n * hi)
        store_path = cs.base_path if base else cs.control_path
        arm = "base" if base else "control"
        out: list[tuple[int, bool]] = []
        for q in cs.qa_ordered[a:b]:
            async with sem:
                out.append(
                    await answer_and_grade(
                        q,
                        store_path,
                        top_k=top_k,
                        embed=embed,
                        reader_cls=reader_cls,
                        judge_llm=judge_llm,
                        logger=logger,
                        conv_idx=cs.idx,
                        arm=arm,
                    )
                )
        return out

    nested = await asyncio.gather(*(one_conv(cs) for cs in conv_states))
    return [r for sub in nested for r in sub]


def _overall(res):
    return sum(ok for _, ok in res), len(res)


def _report_subset(pct: int, off, base, stored_base: int, stored_control: int, logger=None) -> None:
    oc, bc = L._by_cat(off), L._by_cat(base)
    cats = sorted(set(oc) | set(bc))

    def cell(d, c):
        x = d.get(c)
        return f"{x[0]}/{x[1]} ({x[0] / x[1]:.0%})" if x else "-"

    lines = [
        "=" * 84,
        f"LoCoMo SCALING — subset = {pct}% of full answerable QA (all 10 conversations)",
        "  READ op fixed (spontaneous recall + agentic search/recall); only the WRITE op differs:",
        "    CONTROL = no-write  (raw conversation stored, recalled)",
        "    BASE    = write     (agent-authored memories, recalled)",
        "=" * 84,
        f"  {'arm':28s} {'overall':>11s} "
        + " ".join(f"{L.CAT_NAME.get(c, str(c)):>11s}" for c in cats),
    ]
    for label, res, by in [("CONTROL (raw, no write)", off, oc), ("BASE (agent write)", base, bc)]:
        o, t = _overall(res)
        cells = " ".join(cell(by, c).rjust(11) for c in cats)
        lines.append(f"  {label:28s} {f'{o}/{t} ({o / max(1, t):.0%})':>11s} {cells}")
    oo, ot = _overall(off)
    bo, bt = _overall(base)
    gain = bo / max(1, bt) - oo / max(1, ot)
    lines += [
        "  " + "-" * 80,
        f"  --> write effect (BASE - CONTROL): {gain:+.0%}   questions={bt}   "
        f"stored: agent={stored_base} vs raw={stored_control}",
        "=" * 84 + "\n",
    ]
    text = "\n".join(lines)
    print("\n" + text, flush=True)
    if logger is not None:
        logger.log_report(text)


async def go(args, stamp: str) -> None:
    path = L.ensure_dataset()
    n_convs = len(json.loads(path.read_text()))
    convs = []
    for i in range(min(n_convs, args.convs)):
        turns, qa = L.load_sample(path, i)
        convs.append((i, turns, balanced_order(qa)))
    total_qa = sum(len(q) for _, _, q in convs)
    model = os.environ.get("ARC_LLM_MODEL")
    print(f"embedder={args.embedder} model={model}")
    print(
        f"conversations={len(convs)}  total answerable QA={total_qa}  "
        f"max_sessions={args.max_sessions}  percents={args.percents}\n",
        flush=True,
    )

    logger = RunLogger(
        {
            "experiment": "locomo_scaling",
            "timestamp": stamp,
            "model": model,
            "embedder": args.embedder,
            "convs": len(convs),
            "total_answerable_qa": total_qa,
            "percents": args.percents,
            "top_k": args.top_k,
            "max_sessions": args.max_sessions,
            "max_rounds": args.max_rounds,
            "answer_max_rounds": ANSWER_MAX_ROUNDS,
            "concurrency": args.concurrency,
            "read_pipeline": "spontaneous + agentic search/recall",
            "arms": {"BASE": "agent-authored memories", "CONTROL": "raw conversation (write_raw)"},
            "reuse_stores": args.reuse_stores,
        },
        stamp,
    )

    sem = asyncio.Semaphore(args.concurrency)
    judge_llm = build_llm()
    reader_cls = make_reader_agent(
        _instrument_llm(build_llm())
    )  # instrumented for trajectory capture

    if args.reuse_stores:
        # Reuse already-built (durable) stores from a prior run — skip the ~2h build phase.
        log.info("reusing prebuilt stores from %s", args.reuse_stores)
        states = []
        for i, _turns, qa_ordered in convs:
            bp = os.path.join(args.reuse_stores, f"base_{i}.sqlite")
            cp = os.path.join(args.reuse_stores, f"control_{i}.sqlite")
            if not (os.path.exists(bp) and os.path.exists(cp)):
                raise SystemExit(
                    f"--reuse-stores: missing store files for conv {i} in {args.reuse_stores}"
                )
            states.append(
                ConvState(i, qa_ordered, bp, cp, _count_memories(bp), _count_memories(cp))
            )
    else:
        workdir = str(logger.stores_dir)  # store files live in the (durable) results dir, not /tmp
        log.info(
            "building stores for %d conversations (concurrency=%d) in %s ...",
            len(convs),
            args.concurrency,
            workdir,
        )
        states = await asyncio.gather(
            *(
                build_conv(
                    i,
                    turns,
                    embed=args.embedder,
                    top_k=args.top_k,
                    max_sessions=args.max_sessions,
                    sem=sem,
                    max_rounds=args.max_rounds,
                    workdir=workdir,
                    logger=logger,
                )
                for i, turns, _ in convs
            )
        )
        for cs, (_, _, qa_ordered) in zip(states, convs, strict=True):
            cs.qa_ordered = qa_ordered
    stored_base = sum(cs.stored_base for cs in states)
    stored_control = sum(cs.stored_control for cs in states)
    log.info(
        "stored across %d conversations: agent=%d  raw=%d", len(states), stored_base, stored_control
    )

    # ---- ramp: answer disjoint bands, report cumulatively ----
    prev = 0.0
    cum_off: list[tuple[int, bool]] = []
    cum_base: list[tuple[int, bool]] = []
    for pct in args.percents:
        hi = pct / 100
        cum_base += await run_band(
            states,
            prev,
            hi,
            base=True,
            top_k=args.top_k,
            judge_llm=judge_llm,
            sem=sem,
            reader_cls=reader_cls,
            embed=args.embedder,
            logger=logger,
        )
        cum_off += await run_band(
            states,
            prev,
            hi,
            base=False,
            top_k=args.top_k,
            judge_llm=judge_llm,
            sem=sem,
            reader_cls=reader_cls,
            embed=args.embedder,
            logger=logger,
        )
        _report_subset(pct, cum_off, cum_base, stored_base, stored_control, logger=logger)
        bo, bt = _overall(cum_base)
        oo, ot = _overall(cum_off)
        logger.write_summary(
            {
                "last_subset_pct": pct,
                "questions": bt,
                "stored_base": stored_base,
                "stored_control": stored_control,
                "base_overall": f"{bo}/{bt}",
                "control_overall": f"{oo}/{ot}",
            }
        )
        prev = hi
    logger.close()
    log.info("artifacts written to %s", logger.dir)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="chroma_embedded",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="litellm")
    p.add_argument("--percents", type=int, nargs="+", default=[5, 10, 20, 100])
    p.add_argument("--convs", type=int, default=10, help="number of conversations (<=10)")
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        help="cap sessions ingested per conv (default: all)",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=16,
        help="cap CodeAct memorize tool-call rounds per session (guards against a "
        "reasoning model looping without return_result); None = unbounded",
    )
    p.add_argument("--concurrency", type=int, default=6, help="max concurrent LLM ops")
    p.add_argument(
        "--reuse-stores",
        type=str,
        default=None,
        help="path to a prior run's stores/ dir; reuse those built stores and skip "
        "the build phase (answer-ramp only)",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    if args.verbose:
        logging.getLogger("nooa.memory").setLevel(logging.DEBUG)

    if not has_llm_creds():
        print(
            "This study is AGENT-AUTHORED and needs an LLM. Set ARC_LLM_* (see llm.py / .env).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")  # time tag per experiment
    asyncio.run(go(args, stamp))


if __name__ == "__main__":
    main()
