# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Long-horizon memory benchmark runner.

Runs the :mod:`tasks` suite as a sequence of dependent "sessions". Between tasks
the agent's short-term context is cleared (a fresh session), so only the
**long-term memory** carries conventions forward — that is the thing under test.

Two solvers:

* ``oracle`` — deterministic; writes the known-good solution and exercises the
  full memory pipeline + monitoring. Runs with **no LLM/keys** (verification path).
* ``llm``    — a real CodeAct agent (gpt-5.4 via the gateway) that writes the code
  itself, using ``self.recall`` / ``self.remember``. Needs ``ARC_LLM_*`` env.

Examples::

    # offline, verifies the harness + monitoring across all backends
    uv run python examples/memory_bench/bench.py --solver oracle --compare --backend sqlite_vec

    # real run with gpt-5.4 + text-embedding-3-large (needs creds, see llm.py)
    uv run python examples/memory_bench/bench.py --solver llm --compare \
        --backend chroma_embedded --embedder litellm --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import build_embedding_config, build_llm, has_embedding_creds, has_llm_creds  # noqa: E402
from nooa_memory import (  # noqa: E402
    MemoryConfig,
    MemoryManager,
    MemoryStats,
    MemoryToolsMixin,
    MemoryType,
    render,
    resolve,
)
from nooa_memory.config import (  # noqa: E402
    ReflectionPolicy,
    RetrievalConfig,
    SpontaneousConfig,
    VectorConfig,
)
from tasks import Task, build_suite  # noqa: E402

from nooa import Agent  # noqa: E402
from nooa.unifiedllm import FakeLLMClient  # noqa: E402

log = logging.getLogger("memory_bench")


# ---------------------------------------------------------------------------
# memory facade for the oracle solver (no-ops when memory is off)
# ---------------------------------------------------------------------------
class MemFacade:
    def __init__(self, manager: MemoryManager | None) -> None:
        self.m = manager

    def remember(self, text: str, type: str = "info", references: list[str] | None = None) -> None:
        if self.m is not None:
            try:
                self.m.remember(text, type=MemoryType(type), references=references)
            except Exception:
                pass

    def recall(self, query: str, k: int = 3) -> list[str]:
        if self.m is None:
            return []
        try:
            return [m.content for m in self.m.recall(query, k=k)]
        except Exception:
            return []

    def recall_rendered(self, query: str, k: int = 3) -> list[str]:
        """Contents plus resolved reference lines (the pass-by-reference view)."""
        if self.m is None:
            return []
        try:
            out: list[str] = []
            for m in self.m.recall(query, k=k):
                out.append(m.content)
                for ref in m.references:
                    out.append(render(resolve(self.m.agent, self.m.store, ref)))
            return out
        except Exception:
            return []


# ---------------------------------------------------------------------------
# agent classes (with / without the memory tools)
# ---------------------------------------------------------------------------
def make_agent_cls(llm, with_memory: bool):
    if with_memory:

        class BenchAgent(MemoryToolsMixin, Agent, llm=llm):
            async def solve(self, instruction: str, workdir: str) -> str:
                """Complete this coding task:

                {instruction}

                Work ONLY inside this directory (use absolute paths): {workdir}

                You have a long-term memory across tasks. BEFORE writing code, call
                self.recall("KVStore file format and public API") to retrieve any
                conventions established in earlier tasks. AFTER finishing, call
                self.remember(fact, type="skill") to save conventions worth reusing.

                Available methods:
                {doc(self)}

                Verify your work by importing the module, then return a one-line summary.
                """
                ...

        return BenchAgent

    class PlainAgent(Agent, llm=llm):
        async def solve(self, instruction: str, workdir: str) -> str:
            """Complete this coding task:

            {instruction}

            Work ONLY inside this directory (use absolute paths): {workdir}

            Verify your work by importing the module, then return a one-line summary.
            """
            ...

    return PlainAgent


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass
class TaskResult:
    name: str
    category: str
    f2p: bool
    f2p_detail: str
    regression_ok: bool
    steps: int


@dataclass
class ConditionResult:
    label: str
    tasks: list[TaskResult] = field(default_factory=list)
    stats: MemoryStats | None = None

    @property
    def f2p_rate(self) -> float:
        return sum(t.f2p for t in self.tasks) / max(1, len(self.tasks))

    @property
    def regression_rate(self) -> float:
        # Only tasks after the first have a regression set; N/A -> treat as 100%.
        applicable = self.tasks[1:]
        if not applicable:
            return 1.0
        return sum(t.regression_ok for t in applicable) / len(applicable)


def _run_regression(task: Task, workdir: Path) -> bool:
    return all(check(workdir)[0] for check in task.p2p)


async def run_condition(
    *,
    label: str,
    solver: str,
    memory_on: bool,
    backend: str,
    embed_force: str,
    fresh_context: bool,
    max_tokens: int | None,
    reasoning_effort: str | None,
    max_tasks: int | None = None,
) -> ConditionResult:
    workdir = Path(tempfile.mkdtemp(prefix="membench_"))
    suite = build_suite()
    if max_tasks:
        suite = suite[:max_tasks]
    llm = build_llm(max_tokens, reasoning_effort) if solver == "llm" else FakeLLMClient()
    agent = make_agent_cls(llm, with_memory=memory_on and solver == "llm")()

    manager: MemoryManager | None = None
    if memory_on:
        cfg = MemoryConfig(
            enabled=True,
            path=":memory:",
            vector=VectorConfig(backend=backend),
            embedding=build_embedding_config(embed_force),
            retrieval=RetrievalConfig(hops=1),
            spontaneous=SpontaneousConfig(query_strategies=("recent_events", "last_message")),
            reflection=ReflectionPolicy(only_top_level=True),
        )
        manager = MemoryManager.install(agent, config=cfg)

    # Count LLM turns via AfterTurn (fires once per turn; survives event clears).
    turns = {"n": 0}
    agent.event_manager.on("AfterTurn", lambda ev: turns.__setitem__("n", turns["n"] + 1))

    result = ConditionResult(label=label)
    for task in suite:
        if fresh_context:
            agent.event_manager.clear()  # new "session": only long-term memory persists
        steps = 1
        try:
            if solver == "oracle":
                task.oracle(workdir, MemFacade(manager))
                if manager is not None:
                    manager.reflect()  # oracle has no agent_call to trigger reflection
            else:
                before = turns["n"]
                await agent.solve(task.instruction, str(workdir))
                steps = turns["n"] - before
        except Exception as e:  # noqa: BLE001
            log.warning("task %s solver error: %r", task.name, e)

        f2p_ok, detail = task.f2p(workdir)
        reg_ok = _run_regression(task, workdir)
        result.tasks.append(TaskResult(task.name, task.category, f2p_ok, detail, reg_ok, steps))
        log.info(
            "[%s] %-18s %-12s f2p=%s reg=%s steps=%d (%s)",
            label,
            task.name,
            task.category,
            f2p_ok,
            reg_ok,
            steps,
            detail,
        )

    if manager is not None:
        result.stats = manager.memory_stats()
        manager.log_summary()
        manager.uninstall()
    return result


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def print_report(conditions: list[ConditionResult]) -> None:
    print("\n" + "=" * 78)
    print("LONG-HORIZON MEMORY BENCHMARK — results")
    print("=" * 78)
    for c in conditions:
        print(f"\n## {c.label}")
        print(f"  {'task':20s} {'category':13s} {'f2p':5s} {'regression':11s} steps")
        for t in c.tasks:
            print(
                f"  {t.name:20s} {t.category:13s} "
                f"{'PASS' if t.f2p else 'FAIL':5s} {'ok' if t.regression_ok else 'BROKE':11s} {t.steps}"
            )
        print(
            f"  -> requirement-fulfillment (f2p): {c.f2p_rate:.0%}   "
            f"regression-avoidance (p2p): {c.regression_rate:.0%}"
        )
        if c.stats is not None:
            print(f"  -> memory usage: {c.stats.summary()}")
    if len(conditions) == 2:
        off, on = conditions
        print("\n## comparison (memory ON vs OFF)")
        print(f"  f2p:        OFF {off.f2p_rate:.0%}  ->  ON {on.f2p_rate:.0%}")
        print(f"  regression: OFF {off.regression_rate:.0%}  ->  ON {on.regression_rate:.0%}")
    print("=" * 78 + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--solver", choices=["oracle", "llm", "auto"], default="auto")
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    p.add_argument("--memory", choices=["on", "off"], default="on")
    p.add_argument("--compare", action="store_true", help="run memory OFF then ON and compare")
    p.add_argument("--no-fresh-context", dest="fresh_context", action="store_false", default=True)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--max-tasks", type=int, default=None, help="run only the first N tasks")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.verbose:
        logging.getLogger("nooa.memory").setLevel(logging.DEBUG)

    solver = args.solver
    if solver == "auto":
        solver = "llm" if has_llm_creds() else "oracle"
    print(
        f"solver={solver}  backend={args.backend}  embedder={args.embedder}  "
        f"llm_creds={has_llm_creds()}  embed_creds={has_embedding_creds()}"
    )
    if solver == "oracle":
        print(
            "(oracle solver: deterministic; verifies harness + memory plumbing/monitoring. "
            "The real success-lift from memory is measured with --solver llm.)"
        )

    common = {
        "solver": solver,
        "backend": args.backend,
        "embed_force": args.embedder,
        "fresh_context": args.fresh_context,
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.reasoning_effort,
        "max_tasks": args.max_tasks,
    }

    async def go() -> list[ConditionResult]:
        if args.compare:
            off = await run_condition(label="memory OFF", memory_on=False, **common)
            on = await run_condition(label="memory ON", memory_on=True, **common)
            return [off, on]
        return [
            await run_condition(
                label=f"memory {args.memory}", memory_on=args.memory == "on", **common
            )
        ]

    print_report(asyncio.run(go()))


if __name__ == "__main__":
    main()
