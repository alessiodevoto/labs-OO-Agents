# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prospective memory: do committed todos fire in LATER sessions?

Session 0 plants N commitments of the form "when <cue event> happens, do
<action>" as ``type="todo"`` memories. Sessions 1..N each announce ONE cue
(plus an unrelated filler task) with short-term context wiped — the agent is
never re-told the commitment. A commitment *fires* when the agent performs the
committed action in the session where its cue appears.

Metrics:
* fire rate    — commitments acted on in their cue session (the headline)
* false fires  — actions performed in a session whose cue was for a DIFFERENT
                 commitment (todo spam / over-triggering)
* closed rate  — fired todos marked DONE (lifecycle hygiene)

Arms (the inject_open_todos A/B from the design plan §2.3):
* OFF        — no memory: fire rate is 0 by construction (the floor)
* relevant   — todos surface only when similar to the session's text
* always     — open todos are injected every task (true prospective memory)

Run::

    uv run python examples/memory_bench/todo_prospective.py                  # auto
    uv run python examples/memory_bench/todo_prospective.py --solver oracle  # offline
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import make_agent_cls  # noqa: E402
from llm import build_embedding_config, build_llm, has_llm_creds  # noqa: E402
from nooa_memory import (  # noqa: E402
    MemoryConfig,
    MemoryManager,
    MemoryType,
)
from nooa_memory.config import SpontaneousConfig, VectorConfig  # noqa: E402

from nooa.events import Task  # noqa: E402
from nooa.unifiedllm import FakeLLMClient  # noqa: E402

log = logging.getLogger("todo_prospective")

# Each commitment: plant text (session 0), the cue announced later, and the
# committed action (a verifiable marker file the agent must create).
COMMITMENTS = [
    {
        "plant": "When the schema migration finishes, create the file MIGRATION-NOTES.txt "
        "containing the word 'migrated'.",
        "cue": "Heads up: the schema migration finished successfully this morning.",
        "marker": "MIGRATION-NOTES.txt",
        "token": "migrated",
    },
    {
        "plant": "When the security audit report arrives, create the file AUDIT-ACK.txt "
        "containing the word 'acknowledged'.",
        "cue": "The security audit report just arrived in the shared folder.",
        "marker": "AUDIT-ACK.txt",
        "token": "acknowledged",
    },
    {
        "plant": "When the v2 API goes live, create the file V2-CHECKLIST.txt "
        "containing the word 'verified'.",
        "cue": "Deployment update: the v2 API went live an hour ago.",
        "marker": "V2-CHECKLIST.txt",
        "token": "verified",
    },
    {
        "plant": "When the design review is approved, create the file DESIGN-FOLLOWUP.txt "
        "containing the word 'scheduled'.",
        "cue": "Good news — the design review was approved by the committee.",
        "marker": "DESIGN-FOLLOWUP.txt",
        "token": "scheduled",
    },
]

# Distractor memories planted alongside the todos: a realistic store where
# 'relevant' mode has competition for the associative block's top-k slots.
DISTRACTORS = [
    "The staging deployment pipeline runs nightly at 02:00 UTC.",
    "Deployment rollbacks use the make undeploy target.",
    "The report generator writes weekly summaries to reports/weekly.md.",
    "Schema changes must be reviewed by the data platform team.",
    "The shared folder is mounted read-only on worker nodes.",
    "API rate limits are configured in gateway/limits.yaml.",
    "Design docs live under docs/design with one folder per system.",
    "The committee meets every second Thursday for roadmap review.",
    "Security scans run in CI via the trufflehog job.",
    "Morning standup notes are appended to notes/standup.md.",
    "The v1 API is frozen; only critical fixes are backported.",
    "Successful migrations are announced in the #data-eng channel.",
]

# Fillers are lexically distinct from the plants (no create/file/containing):
# a shared generic verb would let every commitment "match" every session.
FILLER_TASKS = [
    "Write a haiku about autumn into {wd}/haiku.txt.",
    "Put the sum of 17 and 25 into {wd}/sum.txt.",
    "Write the alphabet reversed into {wd}/alpha.txt.",
    "Put a one-line greeting into {wd}/greet.txt.",
]


def _cue_matches(session_text: str, todo_content: str) -> bool:
    """A minimally-judging agent: act only when the session shares a distinctive
    (len>=4) token with the commitment — surfacing alone is not a trigger."""
    distinctive = {t for t in session_text.lower().split() if len(t.strip(".,:;!—")) >= 4}
    todo_tokens = {t.strip(".,:;!—'\"") for t in todo_content.lower().split()}
    return bool(distinctive & todo_tokens)


def _install(agent, *, mode: str, backend: str, embed: str) -> MemoryManager:
    return MemoryManager.install(
        agent,
        config=MemoryConfig(
            enabled=True,
            path=":memory:",
            vector=VectorConfig(backend=backend),
            embedding=build_embedding_config(embed),
            spontaneous=SpontaneousConfig(
                inject_open_todos=mode,  # "relevant" | "always" | "off"
                query_strategies=("last_message", "recent_events"),
                inject_cadence="every_turn",
            ),
        ),
    )


async def run_arm(*, arm: str, solver: str, backend: str, embed: str) -> dict:
    """One arm over all commitments; returns {fired, false_fires, closed, needed}."""
    memory_on = arm != "OFF"
    llm = build_llm() if solver == "llm" else FakeLLMClient()
    agent = make_agent_cls(llm, with_memory=memory_on and solver == "llm")()
    manager = _install(agent, mode=arm, backend=backend, embed=embed) if memory_on else None
    wd = Path(tempfile.mkdtemp(prefix="todo_prospective_"))

    # --- session 0: plant the commitments (+ a realistic store of facts) ---
    if memory_on:
        if solver == "oracle":
            for c in COMMITMENTS:
                manager.remember(c["plant"], type=MemoryType.TODO)
            for d in DISTRACTORS:
                manager.remember(d, type=MemoryType.INFO)
        else:
            plants = "\n".join(f"- {c['plant']}" for c in COMMITMENTS)
            await agent.solve(
                "Record each of these future commitments in long-term memory as a "
                f'todo (self.remember(text, type="todo")) — do NOT act on them yet:\n{plants}',
                str(wd),
            )

    surfaced = 0
    fired = 0
    false_fires = 0
    closed = 0
    # --- sessions 1..N: one cue each, context wiped between sessions ---
    for i, c in enumerate(COMMITMENTS):
        agent.event_manager.clear()
        session_text = f"{c['cue']} Also: {FILLER_TASKS[i].format(wd=wd)}"
        if solver == "oracle":
            # Deterministic policy: read the spontaneous block for this session;
            # act on a surfaced open todo ONLY if its cue plausibly matches the
            # session (surfacing is the memory system's job, judging is the
            # agent's), then close what was done.
            if manager is None:
                continue
            agent.event_manager.add(Task(prompt=session_text))
            block = manager.recall_for_context()
            if c["marker"] in block:
                surfaced += 1  # the RIGHT commitment resurfaced in its cue session
            for cand in COMMITMENTS:
                if cand["marker"] in block and _cue_matches(session_text, cand["plant"]):
                    (wd / cand["marker"]).write_text(cand["token"])
                    for m in manager.store.all_memories():
                        if m.type is MemoryType.TODO and cand["marker"] in m.content:
                            if m.status == "open":
                                manager.update(m.id, status="done")
        else:
            try:
                await agent.solve(
                    f"{session_text}\nHandle anything your memory says is due now, then "
                    "mark completed todos DONE via self.update_memory(id, status='DONE').",
                    str(wd),
                )
            except Exception as e:  # noqa: BLE001
                log.warning("session %d solve error: %r", i, e)

        # grade this session: only c's marker should newly appear
        for cand in COMMITMENTS:
            path = wd / cand["marker"]
            if not path.exists():
                continue
            if cand is c:
                fired += 1
            else:
                false_fires += 1
            path.unlink()  # count each firing once, in its session

    if manager is not None:
        closed = sum(
            1
            for m in manager.store.all_memories()
            if m.type is MemoryType.TODO and m.status == "done"
        )
        log.info("[%s] memory: %s", arm, manager.memory_stats().summary())
        manager.uninstall()
    return {
        "surfaced": surfaced,
        "fired": fired,
        "false_fires": false_fires,
        "closed": closed,
        "needed": len(COMMITMENTS),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--solver", choices=["oracle", "llm", "auto"], default="auto")
    p.add_argument(
        "--backend",
        choices=["numpy", "sqlite_vec", "chroma_embedded", "chroma_http"],
        default="numpy",
    )
    p.add_argument("--embedder", choices=["auto", "hashing", "litellm"], default="auto")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    solver = args.solver
    if solver == "auto":
        solver = "llm" if has_llm_creds() else "oracle"
    print(f"solver={solver}  backend={args.backend}  embedder={args.embedder}\n")

    async def go() -> dict:
        out = {}
        for arm in ("OFF", "relevant", "always"):
            out[arm] = await run_arm(
                arm=arm, solver=solver, backend=args.backend, embed=args.embedder
            )
        return out

    res = asyncio.run(go())

    print("\n" + "=" * 72)
    print("PROSPECTIVE MEMORY: commitment fire rate by injection mode")
    print("=" * 72)
    print(f"  {'arm':10s} {'surfaced':>10s} {'fired':>10s} {'false fires':>12s} {'closed':>8s}")
    for arm, r in res.items():
        print(
            f"  {arm:10s} {r['surfaced']}/{r['needed']:<8d} {r['fired']}/{r['needed']:<8d} "
            f"{r['false_fires']:>12d} {r['closed']:>8d}"
        )
    print("=" * 72)
    print("surfaced = the right todo appeared in its cue session's block (the")
    print("memory system's job); fired = the oracle then acted on a matching cue")
    print("(the agent's job). OFF is 0 by construction.")
    print("'always' should fire every commitment; 'relevant' fires only when the")
    print("cue text is similar enough to the todo — the gap is the A/B signal for")
    print("the inject_open_todos default (design plan §2.3).")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
