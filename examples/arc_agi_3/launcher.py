# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Launch the ARC-AGI-3 solver agent inside the nemo-oo TUI (run me in tmux).

Replicates the essential body of ``nooa_tui.tui.main`` so we can hook
between ``bootstrap()`` and ``session.run()``:  the TUI's own memory wiring is
turned off and, for the ``memory`` variant, a fully-configured ``MemorySkill``
(gateway embeddings, generative reflection) is registered instead.

Usage (normally started by run_solver.py):

    .venv/bin/python examples/arc_agi_3/launcher.py \
        --run-dir results/arc_agi_3/nemo_solver/<run> --game ls20 --variant memory
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXAMPLE_DIR))

from arc_llm import build_embedding_config, build_llm  # noqa: E402
from solver_agent import MdArcSolverAgent, MemArcSolverAgent  # noqa: E402

SKILL_PATH = EXAMPLE_DIR / "skills" / "grid-game-solver" / "SKILL.md"


def parse_effort_ladder(
    spec: str | None, primary_effort: str | None
) -> list[tuple[float, str]] | None:
    """Parse ``"600:medium,900:low"`` into an ascending effort ladder.

    Rung 0 is the client's primary effort (``primary_effort``; may be None =
    model default); each ``after_seconds:effort`` pair is a downshift rung the
    agent applies once a turn has run that long. Returns None when there is no
    downshift rung to apply (so the agent keeps a single fixed effort).
    """
    rungs: list[tuple[float, str]] = [(0.0, primary_effort or "")]
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        after, _, effort = part.partition(":")
        rungs.append((float(after), effort.strip()))
    return sorted(rungs) if len(rungs) > 1 else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--game", required=True)
    p.add_argument("--variant", choices=["memory", "mdfiles"], required=True)
    p.add_argument("--reflect-every", type=int, default=8)
    p.add_argument(
        "--alias", default="", help="opaque handle the agent sees instead of the game id"
    )
    p.add_argument(
        "--model",
        default=None,
        help="gateway model id override, e.g. openai/openai/gpt-5.5 "
        "(default: ARC_LLM_MODEL from .env)",
    )
    p.add_argument(
        "--reasoning-effort", default=None, help="primary reasoning effort (ladder rung 0)"
    )
    p.add_argument(
        "--effort-ladder",
        default=None,
        help="downshift rungs 'after_s:effort[,after_s:effort…]', e.g. "
        "'600:medium' — reasoning_effort drops to that level once a "
        "turn has run that long (single model, no second client)",
    )
    p.add_argument(
        "--visual",
        choices=["off", "only", "additive"],
        default="off",
        help="grid-as-image mode: off | only (image replaces the hex grid) "
        "| additive (image + hex grid)",
    )
    p.add_argument(
        "--png-scale",
        dest="png_scale",
        type=int,
        default=8,
        help="pixels per grid cell for the visual PNG (grid is "
        "(scale*64)x(scale*64); typically 8-16)",
    )
    p.add_argument(
        "--seed-knowledge",
        default=None,
        help="Prior run's workspace to seed knowledge from "
        "(memory.sqlite / knowledge/*.md are copied in)",
    )
    return p.parse_args()


def seed_knowledge(src: Path, workspace: Path, variant: str) -> None:
    """Copy accumulated knowledge from a prior run's workspace into this one."""
    import shutil

    if variant == "memory":
        db = src / "memory.sqlite"
        if db.exists():
            shutil.copy2(db, workspace / "memory.sqlite")
    else:
        src_knowledge = src / "knowledge"
        if src_knowledge.is_dir():
            (workspace / "knowledge").mkdir(parents=True, exist_ok=True)
            for f in src_knowledge.glob("*.md"):
                shutil.copy2(f, workspace / "knowledge" / f.name)
    src_helpers = src / "helpers"
    if src_helpers.is_dir():
        (workspace / "helpers").mkdir(parents=True, exist_ok=True)
        for f in src_helpers.glob("*.py"):
            shutil.copy2(f, workspace / "helpers" / f.name)


async def run() -> None:
    args = parse_args()
    import os

    if args.model:
        os.environ["ARC_LLM_MODEL"] = args.model
    run_dir = Path(args.run_dir).resolve()
    workspace = run_dir / "team_nemo" / "shared"
    workspace.mkdir(parents=True, exist_ok=True)

    # Tracing: JSONL into the run dir, plus OTLP when OTLP_ENDPOINT is set
    # (e.g. http://localhost:22006/v1/traces for a local trace server).
    from viewer_event_exporter import ViewerEventExporter
    from viewer_trace_exporter import ViewerMessageExporter

    from nooa.tracing import enable_tracing, exporters

    trace_exporters = [
        exporters.jsonl(trace_dir=str(run_dir / "traces")),
        # writes the REAL prompts/responses into agent_logs/.../messages LIVE,
        # so both viewers show the real conversation as it happens.
        ViewerMessageExporter(run_dir),
        # translates live spans -> reference-schema events (llm_call / repl_execute
        # / round_complete / step_complete) into events.jsonl, so the TUI's
        # reasoning / REPL / rounds panels populate.
        ViewerEventExporter(run_dir),
    ]
    otlp_endpoint = os.environ.get("OTLP_ENDPOINT")
    if otlp_endpoint:
        trace_exporters.append(exporters.otlp(otlp_endpoint))
    enable_tracing(exporters=trace_exporters)

    if args.seed_knowledge:
        seed_knowledge(Path(args.seed_knowledge).resolve(), workspace, args.variant)

    try:
        from nooa_tui.tui.bootstrap import (
            bootstrap,
            build_registry,
            build_session,
            build_startup_info,
        )
        from nooa_tui.tui.config import Config
        from nooa_tui.tui.frontend import TerminalFrontend
    except ImportError as e:
        raise SystemExit(
            "launcher.py drives the agent inside the nooa TUI, which is a "
            "separate internal package (nooa-tui, in the nooa-dev repo). "
            "Install it into this environment to use the TUI runner. "
            f"(import failed: {e})"
        ) from e

    config = Config.load(
        model=None,
        agent=None,
        no_splash=True,
        working_dir=str(workspace),
        mcp_file=None,
        skills_dir=[str(EXAMPLE_DIR / "skills")],
        trace=None,
        no_trace=True,  # tracing is wired explicitly above
        context_limit=None,
        orchestrator=False,
        vi=False,
    )
    # The TUI's own memory wiring can't take our embedding config — install
    # MemorySkill ourselves (memory variant) after bootstrap instead.
    config.tui.memory = "off"

    llm = build_llm(reasoning_effort=args.reasoning_effort)
    effort_ladder = parse_effort_ladder(args.effort_ladder, args.reasoning_effort)
    agent_cls = MemArcSolverAgent if args.variant == "memory" else MdArcSolverAgent
    # The agent process never receives the real game id — only the opaque alias
    # (passed as both game_id and alias so no self.game_id path can leak identity).
    _alias = args.alias or "the game"
    agent = agent_cls(
        llm=llm,
        run_dir=run_dir,
        game_id=_alias,
        alias=_alias,
        reflect_every=args.reflect_every,
        effort_ladder=effort_ladder,
        visual=args.visual,
        png_scale=args.png_scale,
        skill_path=SKILL_PATH if SKILL_PATH.exists() else None,
    )

    frontend = TerminalFrontend(config)
    result = await bootstrap(config, agent=agent)

    # Memory store lives at a NEUTRAL path (opaque alias, no benchmark/game name),
    # because the memory-skill guide shows the store path in the agent's context.
    # A workspace path (results/arc_agi_3/<...>_<game>_<variant>/) would leak both
    # `arc_agi_3` and the game id to the memory agent. Copied back into the
    # workspace at run end for the viewer / seeding / analysis.
    import re
    import shutil

    safe_alias = re.sub(r"[^a-z0-9_-]", "_", (args.alias or "the-game").lower())
    store_path = Path(tempfile.gettempdir()) / "agent_stores" / f"{safe_alias}.sqlite"
    ws_store = workspace / "memory.sqlite"

    if args.variant == "memory":
        from nooa_memory import MemoryConfig
        from nooa_memory.config import (
            ReflectionPolicy,
            RetrievalConfig,
            SpontaneousConfig,
        )
        from nooa_memory.generative import llm_reasoner, llm_reconciler
        from nooa_memory.memory_skill import MemorySkill

        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.unlink(missing_ok=True)
        # If seeded, the seed's memory.sqlite was copied into the workspace; move
        # it to the neutral live path so the run starts seeded but the agent never
        # sees the workspace path.
        if ws_store.exists():
            shutil.move(str(ws_store), str(store_path))

        def _session_llm() -> object:
            return agent._llm

        memory_config = MemoryConfig(
            enabled=True,
            path=str(store_path),
            owner="ArcSolver",
            embedding=build_embedding_config(),
            retrieval=RetrievalConfig(top_k=8, hops=1),
            spontaneous=SpontaneousConfig(enabled=True, top_k=6),
            reflection=ReflectionPolicy(trigger="manual", background=False),
        )
        agent.skills.register(
            "nemo.memory",
            MemorySkill(
                memory_config,
                reasoner=llm_reasoner(_session_llm),
                reconciler=llm_reconciler(_session_llm),
            ),
        )
        agent.skills.activate(["nemo.memory"])

    startup_info = build_startup_info(result)
    registry = build_registry(result, frontend)
    registry.startup_info = startup_info
    frontend.init_input(registry)
    session = build_session(
        result, frontend, registry, initial_outputs=[*result.messages, startup_info]
    )
    try:
        await session.run()
    finally:
        # Consolidate the neutral store and copy it into the workspace so the
        # viewer, analysis tools, and seeded runs find it where they expect.
        if args.variant == "memory" and store_path.exists():
            try:
                import sqlite3

                con = sqlite3.connect(str(store_path))
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.close()
            except Exception:
                pass
            try:
                shutil.copy2(store_path, ws_store)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(run())
