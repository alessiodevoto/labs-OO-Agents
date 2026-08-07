# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end BrowseComp-Plus evaluation driver.

Iterates a subset of records, invokes a :class:`BrowseCompAgent` per query, and
grades the response. Deliberately separate from the general Harbor runner --
BrowseComp-Plus is not container-based and its "task input" is just a query
string.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from nooa_bench.browsecomp.dataset import BrowseCompRecord, load_records
from nooa_bench.browsecomp.grader import GradeResult, Grader, HeuristicGrader


@dataclass
class RunReport:
    n: int
    accuracy: float
    error_count: int
    per_query: list[dict] = field(default_factory=list)

    def dump(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))


AgentFactory = Callable[[], "object"]  # returns something with async _run_evaluation


async def _run_one(
    agent_factory: AgentFactory,
    record: BrowseCompRecord,
    grader: Grader,
) -> dict:
    agent = agent_factory()
    task_input = {"query": record.query, "query_id": record.query_id}
    try:
        out = await agent._run_evaluation(task_input)
    except Exception as exc:  # noqa: BLE001
        out = {"response": "", "success": False, "error": repr(exc)}

    response = out.get("response", "") or ""
    err = out.get("error")
    if err:
        grade = GradeResult(correct=False, extracted_answer="", reasoning=err)
    else:
        grade = grader.grade(record.query, response, record.answer)

    return {
        "query_id": record.query_id,
        "query": record.query,
        "gold_answer": record.answer,
        "response": response,
        "extracted_answer": grade.extracted_answer,
        "correct": grade.correct,
        "reasoning": grade.reasoning,
        "result": out.get("result"),
        "error": err,
    }


async def evaluate_async(
    agent_factory: AgentFactory,
    records: list[BrowseCompRecord],
    *,
    grader: Grader | None = None,
    on_progress: Callable[[int, int, bool], None] | None = None,
    concurrency: int = 1,
) -> RunReport:
    """Run ``agent_factory()`` on every record, grade, aggregate.

    A fresh agent is instantiated per query (matches how ``BenchAgent`` is used
    in nooa-bench today -- one task, one agent). ``concurrency`` caps how many
    queries run in parallel; keep it small when hitting a paid API.
    """

    grader = grader or HeuristicGrader()
    sem = asyncio.Semaphore(concurrency)

    async def _slot(i: int, r: BrowseCompRecord) -> dict:
        async with sem:
            row = await _run_one(agent_factory, r, grader)
            if on_progress:
                on_progress(i + 1, len(records), row["correct"])
            return row

    per_query = await asyncio.gather(*[_slot(i, r) for i, r in enumerate(records)])
    correct = sum(1 for row in per_query if row["correct"])
    errors = sum(1 for row in per_query if row["error"])
    n = len(records)
    return RunReport(
        n=n,
        accuracy=correct / n if n else 0.0,
        error_count=errors,
        per_query=list(per_query),
    )


def evaluate(
    agent_factory: AgentFactory,
    records: list[BrowseCompRecord],
    *,
    grader: Grader | None = None,
    on_progress: Callable[[int, int, bool], None] | None = None,
    concurrency: int = 1,
) -> RunReport:
    """Sync wrapper around :func:`evaluate_async`."""
    return asyncio.run(
        evaluate_async(
            agent_factory,
            records,
            grader=grader,
            on_progress=on_progress,
            concurrency=concurrency,
        )
    )


def load_subset(
    *, limit: int | None = 5, query_ids: list[str] | None = None
) -> list[BrowseCompRecord]:
    """Convenience: load a small deterministic subset for iteration."""
    return load_records(limit=limit, query_ids=query_ids)
