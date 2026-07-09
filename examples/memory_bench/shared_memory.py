# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared stores: owner isolation by default, knowledge transfer on request.

Two agents share ONE memory store. Agent A ("scout") ingests facts about a
project; agent B ("builder") then answers questions about them:

* arm ``isolated`` — B's default own-scope recall: must find NOTHING (the
  correctness property: owners cannot leak into each other by accident).
* arm ``shared``  — B recalls with ``owner="*"``: A's knowledge transfers.
* leakage check   — across ALL of B's default recalls, zero A-owned memories
  may appear (asserted, not just reported).

Run::

    uv run python examples/memory_bench/shared_memory.py                  # auto
    uv run python examples/memory_bench/shared_memory.py --solver oracle  # offline
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
from nooa_tui.memory import MemoryConfig, MemoryManager  # noqa: E402
from nooa_tui.memory.config import VectorConfig  # noqa: E402

from nooa.unifiedllm import FakeLLMClient  # noqa: E402

log = logging.getLogger("shared_memory")

# Facts A learns; questions B must answer; the grading token.
FACTS = [
    {
        "fact": "The ingest service reads its queue name from INGEST_QUEUE_NAME "
        "and the default is 'raw-events-v3'.",
        "question": "what is the default queue name for the ingest service",
        "token": "raw-events-v3",
    },
    {
        "fact": "Production deploys require the release tag format rel-YYYY.MM.DD-N.",
        "question": "what format must a production release tag use",
        "token": "rel-YYYY.MM.DD-N",
    },
    {
        "fact": "The metrics dashboard admin password is stored in vault path "
        "secret/observability/grafana.",
        "question": "where is the metrics dashboard admin password stored",
        "token": "secret/observability/grafana",
    },
    {
        "fact": "Batch jobs must set spark.sql.shuffle.partitions to 400 for the "
        "nightly aggregation.",
        "question": "what shuffle partition count do nightly batch jobs need",
        "token": "400",
    },
    {
        "fact": "The customer export endpoint is rate-limited to 6 requests per minute per tenant.",
        "question": "what is the rate limit on the customer export endpoint",
        "token": "6 requests",
    },
    {
        "fact": "Incident postmortems are filed under ops/postmortems using the "
        "incident id as the filename.",
        "question": "where are incident postmortems filed",
        "token": "ops/postmortems",
    },
]


def _install(agent, *, owner: str, path: str, backend: str, embed: str) -> MemoryManager:
    return MemoryManager.install(
        agent,
        config=MemoryConfig(
            enabled=True,
            path=path,
            owner=owner,
            vector=VectorConfig(backend=backend),
            embedding=build_embedding_config(embed),
        ),
    )


async def run(*, solver: str, backend: str, embed: str) -> dict:
    shared_db = str(Path(tempfile.mkdtemp(prefix="shared_memory_")) / "team-memory.sqlite")
    llm = build_llm() if solver == "llm" else FakeLLMClient()

    # --- agent A (scout) ingests the facts ---
    scout = make_agent_cls(llm, with_memory=solver == "llm")()
    mgr_a = _install(scout, owner="scout", path=shared_db, backend=backend, embed=embed)
    if solver == "oracle":
        for f in FACTS:
            mgr_a.remember(f["fact"])  # manager-level API: MemoryType, default INFO
    else:
        facts = "\n".join(f"- {f['fact']}" for f in FACTS)
        await scout.solve(
            f"Save each of these project facts to long-term memory, one memory each:\n{facts}",
            tempfile.mkdtemp(prefix="scout_"),
        )

    # --- agent B (builder) answers, on the same store ---
    builder = make_agent_cls(llm, with_memory=solver == "llm")()
    mgr_b = _install(builder, owner="builder", path=shared_db, backend=backend, embed=embed)

    isolated_correct = 0
    shared_correct = 0
    leaked = 0
    for f in FACTS:
        own = mgr_b.recall(f["question"], k=3)  # default: builder's own scope
        leaked += sum(1 for m in own if m.owner == "scout")
        if any(f["token"].lower() in m.content.lower() for m in own):
            isolated_correct += 1

        everyone = mgr_b.recall(f["question"], k=3, owner="*")
        if any(f["token"].lower() in m.content.lower() for m in everyone):
            shared_correct += 1

    stats_b = mgr_b.memory_stats()
    log.info("[builder] memory: %s", stats_b.summary())
    mgr_a.uninstall()
    mgr_b.uninstall()
    return {
        "n": len(FACTS),
        "isolated": isolated_correct,
        "shared": shared_correct,
        "leaked": leaked,
        "cross_owner_recalls": stats_b.cross_owner_recalls,
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

    r = asyncio.run(run(solver=solver, backend=args.backend, embed=args.embedder))

    print("\n" + "=" * 72)
    print("SHARED MEMORY: isolation by default, transfer on request")
    print("=" * 72)
    print(f"  builder, own scope (default) : {r['isolated']}/{r['n']} answered")
    print(f"  builder, owner='*' (shared)  : {r['shared']}/{r['n']} answered")
    print(f"  leaked scout memories in default scope: {r['leaked']} (MUST be 0)")
    print(f"  cross-owner recalls counted  : {r['cross_owner_recalls']}")
    print("=" * 72)
    verdict_iso = "ISOLATED" if r["isolated"] == 0 and r["leaked"] == 0 else "LEAKY (BUG)"
    verdict_share = "TRANSFERS" if r["shared"] == r["n"] else "incomplete transfer"
    print(f"  default scope: {verdict_iso}   |   owner='*': {verdict_share}")
    print("=" * 72 + "\n")
    if r["leaked"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
