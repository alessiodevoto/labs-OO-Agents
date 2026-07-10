# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Orchestrate one ARC-AGI-3 solving run: tmux'd TUI agent + environment harness.

Stdlib-only — run with any python:

    python examples/arc_agi_3/run_solver.py --game ls20 --variant memory

What it does:
1. creates ``results/arc_agi_3/nemo_solver/<ts>_<game>_<variant>/`` (+ ipc/),
2. starts a tmux session running launcher.py (the nemo-oo TUI agent) — attach
   with ``tmux attach -t <session>`` to watch or type guidance to the agent,
3. starts harness.py in this venv (the `arc` extra provides the arcade SDK),
4. once the harness publishes state 0, sends the kickoff message into the TUI,
5. waits for the harness to finish and prints the result summary.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXAMPLE_DIR))  # for `from sandbox import ...`
# The agent (launcher.py) and the environment harness (harness.py) both run in
# THIS interpreter. Install the arcade SDK with `pip install "nemo-oo-agents[arc]"`.
MAIN_PY = sys.executable
DATA_DIR = REPO_ROOT  # offline games are downloaded here under environment_files/


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--game", required=True, help="game id, e.g. ls20")
    p.add_argument("--variant", choices=["memory", "mdfiles"], required=True)
    p.add_argument("--results-root", default=str(REPO_ROOT / "results" / "arc_agi_3"))
    p.add_argument(
        "--group", default="nemo_solver", help="grouping dir under results-root (viewer type_dir)"
    )
    p.add_argument(
        "--max-turns",
        default=None,
        help="max agent turns; None/unlimited by default (pass an int to cap)",
    )
    p.add_argument(
        "--max-env-steps",
        type=int,
        default=5000,
        help="max total environment actions — the main run bound",
    )
    p.add_argument(
        "--allowed-game-overs",
        type=int,
        default=-1,
        help="harness auto-RESET budget after GAME_OVER (-1 unlimited)",
    )
    p.add_argument(
        "--agent-turn-timeout",
        type=float,
        default=1200.0,
        help="agent silence before agent_timeout (kill rung)",
    )
    p.add_argument(
        "--nudge-after",
        type=float,
        default=900.0,
        help="agent silence before the harness writes one reminder state",
    )
    p.add_argument(
        "--fallback-window",
        type=float,
        default=90.0,
        help="seconds after the urgent nudge before the harness "
        "force-advances with a default action (never terminates)",
    )
    p.add_argument(
        "--effort-ladder",
        default="600:medium",
        help="reasoning-effort downshift rungs 'after_s:effort', e.g. "
        "'600:medium' (single model; '' disables)",
    )
    p.add_argument("--reflect-every", type=int, default=8)
    p.add_argument(
        "--model", default=None, help="gateway model id override, e.g. openai/openai/gpt-5.5"
    )
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument(
        "--visual",
        choices=["off", "only", "additive"],
        default="off",
        help="grid-as-image mode: off | only (image replaces hex grid) | "
        "additive (image + hex grid)",
    )
    p.add_argument(
        "--png-scale",
        dest="png_scale",
        type=int,
        default=8,
        help="pixels per grid cell for the visual PNG (typically 8-16)",
    )
    p.add_argument(
        "--seed-knowledge",
        default=None,
        help="prior run's team_nemo/shared dir to seed knowledge from",
    )
    p.add_argument("--operation-mode", default="offline")
    p.add_argument(
        "--scorecard-id",
        default="",
        help="competition shared scorecard id (forwarded to the harness)",
    )
    p.add_argument(
        "--event-prompt",
        choices=["off", "count"],
        default="count",
        help="animation-event summary in the agent's state (off|count)",
    )
    p.add_argument(
        "--kill-tmux", action="store_true", help="kill the tmux session when the run ends"
    )
    p.add_argument("--tag", default="", help="extra tag appended to the run dir name")
    p.add_argument(
        "--tmux-session",
        default="",
        help="explicit tmux session name (default: auto). The multi-runner "
        "sets this so it can tear the session down on TUI quit.",
    )
    p.add_argument(
        "--tmux-socket",
        default="",
        help="tmux socket name (tmux -L). Puts this run's sessions on a "
        "private, per-run tmux server so they never show up in a plain "
        "`tmux ls` (view with `tmux -L <socket> ls`). Default: shared "
        "default socket.",
    )
    p.add_argument(
        "--sandbox",
        choices=["off", "inproc", "full"],
        default="inproc",
        help="off: no guards; inproc: L1+L2 in-process (default, always on "
        "via the agent); full: also wrap the launcher in the L3 OS "
        "sandbox (requires user namespaces — fails closed if absent)",
    )
    return p.parse_args()


# Set once in main() from --tmux-socket; every tmux() call targets this private
# per-run server (tmux -L) so the game sessions stay out of the default `tmux ls`.
_TMUX_SOCKET = ""


def tmux(*cmd: str) -> subprocess.CompletedProcess:
    sock = ["-L", _TMUX_SOCKET] if _TMUX_SOCKET else []
    return subprocess.run(["tmux", *sock, *cmd], capture_output=True, text=True)


def _set_parent_death_signal() -> None:
    """Linux best-effort: get SIGTERM if our parent (run_multi) dies — even on an
    unhandleable SIGKILL of the orchestrator — so a game never outlives its runner.
    Our SIGTERM handler then reaps the harness + tmux agent."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG = 1
    except Exception:
        pass


def _load_dotenv() -> None:
    """Minimal .env loader so ARC_API_KEY / ARC_SDK_SESSION_COOKIES reach the
    harness subprocess (competition mode) even on direct invocation. Real env wins."""
    f = REPO_ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main() -> int:
    _set_parent_death_signal()  # die with run_multi (covers its SIGKILL)
    _load_dotenv()
    args = parse_args()
    global _TMUX_SOCKET
    _TMUX_SOCKET = args.tmux_socket
    ts = time.strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    run_name = f"{ts}_{args.game}_{args.variant}{tag}"
    run_dir = Path(args.results_root) / args.group / run_name
    # Opaque per-run handle the agent sees instead of the real game id. Derived
    # from run metadata only (timestamp/variant) — never from the game name — so
    # it carries no game identity.
    import hashlib

    alias = "game-" + hashlib.sha1(f"{ts}{args.variant}{tag}".encode()).hexdigest()[:6]
    ipc = run_dir / "ipc"
    ipc.mkdir(parents=True, exist_ok=True)
    (run_dir / "team_nemo" / "shared").mkdir(parents=True, exist_ok=True)
    (ipc / "states.jsonl").touch()
    (ipc / "actions.jsonl").touch()

    session = args.tmux_session or f"arc3_{args.game}_{args.variant}{tag}_{ts}"
    otlp = os.environ.get("OTLP_ENDPOINT", "")
    inner = (
        f"{MAIN_PY} examples/arc_agi_3/launcher.py"
        f" --run-dir {run_dir} --game {args.game} --variant {args.variant}"
        f" --alias {alias}"
        f" --reflect-every {args.reflect_every}"
        + (f" --model {args.model}" if args.model else "")
        + (f" --reasoning-effort {args.reasoning_effort}" if args.reasoning_effort else "")
        + (f" --effort-ladder {args.effort_ladder}" if args.effort_ladder else "")
        + f" --visual {args.visual} --png-scale {args.png_scale}"
        + (f" --seed-knowledge {args.seed_knowledge}" if args.seed_knowledge else "")
    )
    # L3: wrap the launcher in the OS sandbox when requested. Fails closed — if
    # namespaces are unavailable, run_solver aborts rather than run unsandboxed.
    if args.sandbox == "full":
        import shlex

        from sandbox import SandboxSpec, SandboxUnavailable, wrap

        spec = SandboxSpec(
            run_dir=run_dir,
            repo_root=REPO_ROOT,
            llm_socket=run_dir / "ipc" / "llm.sock",
            tmp_dir=run_dir / "agent-tmp",
        )
        (run_dir / "agent-tmp").mkdir(exist_ok=True)
        # Pre-create the launcher's own writable dirs: the sandbox binds them rw,
        # and bwrap's --bind fails on a missing source (the launcher writes its
        # events/messages under agent_logs and JSONL traces under traces).
        (run_dir / "agent_logs").mkdir(exist_ok=True)
        (run_dir / "traces").mkdir(exist_ok=True)
        try:
            inner = " ".join(shlex.quote(a) for a in wrap(shlex.split(inner), spec))
        except SandboxUnavailable as e:
            print(f"[run] {e}", file=sys.stderr)
            return 3
        print("[run] launcher wrapped in L3 OS sandbox")
    launcher_cmd = (
        f"cd {REPO_ROOT} && "
        + (f"OTLP_ENDPOINT={otlp} " if otlp else "")
        + inner
        + f" 2>{run_dir}/launcher.err"
    )
    r = tmux("new-session", "-d", "-s", session, "-x", "220", "-y", "50", launcher_cmd)
    if r.returncode != 0:
        print(f"failed to start tmux session: {r.stderr}", file=sys.stderr)
        return 2
    _sockarg = f"-L {_TMUX_SOCKET} " if _TMUX_SOCKET else ""
    print(
        f"[run] TUI agent starting in tmux session {session!r}"
        + (f" (socket {_TMUX_SOCKET})" if _TMUX_SOCKET else "")
    )
    print(f"[run]   watch live:  tmux {_sockarg}attach -t {session}")
    print(f"[run]   run dir:     {run_dir}")

    llm_uri = args.model or os.environ.get("ARC_LLM_MODEL", "")
    if args.reasoning_effort:
        llm_uri = f"{llm_uri}#{args.reasoning_effort}"
    if not llm_uri and (REPO_ROOT / ".env").exists():
        for line in (REPO_ROOT / ".env").read_text().splitlines():
            if line.startswith("ARC_LLM_MODEL="):
                llm_uri = line.split("=", 1)[1].strip()
                break
    harness_cmd = [
        sys.executable,
        str(EXAMPLE_DIR / "harness.py"),
        "--run-dir",
        str(run_dir),
        "--game",
        args.game,
        "--variant",
        args.variant,
        "--alias",
        alias,
        "--llm-uri",
        llm_uri,
        "--operation-mode",
        args.operation_mode,
        "--max-turns",
        str(args.max_turns),
        "--max-env-steps",
        str(args.max_env_steps),
        "--allowed-game-overs",
        str(args.allowed_game_overs),
        "--agent-turn-timeout",
        str(args.agent_turn_timeout),
        "--nudge-after",
        str(args.nudge_after),
        "--fallback-window",
        str(args.fallback_window),
        "--scorecard-id",
        args.scorecard_id,
        "--event-prompt",
        args.event_prompt,
        "--visual",
        args.visual,
    ]
    # harness.log lives OUTSIDE the run dir: it carries the arcade SDK's boot
    # line ("loaded ... from environment_files/<game>/.../<game>.py"), which would
    # leak the game identity if an agent tailed it. Kept in a sibling _harness/
    # dir the agent's sandbox never mounts.
    harness_log_dir = Path(args.results_root) / args.group / "_harness"
    harness_log_dir.mkdir(parents=True, exist_ok=True)
    harness_log = (harness_log_dir / f"{run_name}.log").open("w")
    harness = subprocess.Popen(
        harness_cmd,
        cwd=str(DATA_DIR),
        stdout=harness_log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    # Reliable teardown on ANY abnormal exit (SIGTERM/SIGHUP/SIGINT, incl. the
    # pdeathsig SIGTERM when run_multi dies): reap the harness AND kill the agent's
    # tmux session so nothing is orphaned. Normal completion below still honors
    # --kill-tmux for the leave-session-for-inspection case.
    _torn = {"done": False}

    def _teardown() -> None:
        if _torn["done"]:
            return
        _torn["done"] = True
        try:
            if harness.poll() is None:
                harness.terminate()
                try:
                    harness.wait(timeout=5)
                except Exception:
                    harness.kill()
        except Exception:
            pass
        tmux("kill-session", "-t", session)  # kill the agent (no-op if already gone)

    def _sig_teardown(signum, _frame):
        print(f"[run] signal {signum} — tearing down game {session}")
        _teardown()
        raise SystemExit(128 + signum)

    for _s in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(_s, _sig_teardown)
        except (ValueError, OSError):
            pass

    # Kick off the agent once the harness has published state 0 (which it only
    # does after the agent's tail producer wrote ipc/agent_ready).
    kicked = False
    states = ipc / "states.jsonl"
    try:
        while harness.poll() is None:
            if not kicked and states.stat().st_size > 0:
                time.sleep(3)  # let the TUI finish drawing before typing into it
                tmux(
                    "send-keys",
                    "-t",
                    session,
                    "-l",
                    f"Start solving {alias}. The first game "
                    "state is on your game_states queue. Follow the arc_skill "
                    "context block.",
                )
                tmux("send-keys", "-t", session, "Enter")
                kicked = True
                print("[run] kickoff message sent to the agent")
            time.sleep(2)
    except KeyboardInterrupt:
        print("[run] interrupted — tearing down game (harness + tmux)")
        _teardown()
    finally:
        harness_log.close()

    # Memory variant: the live store is at a neutral /tmp path (so the agent's
    # memory guide can't leak the benchmark/game name). Copy it back into the
    # workspace now that the game is done, for the viewer / seeding / analysis.
    # Done here (not in the launcher) because the TUI process is about to be
    # killed and won't run its own teardown copy.
    if args.variant == "memory":
        import re
        import shutil
        import tempfile

        safe_alias = re.sub(r"[^a-z0-9_-]", "_", alias.lower())
        neutral_store = Path(tempfile.gettempdir()) / "agent_stores" / f"{safe_alias}.sqlite"
        if neutral_store.exists():
            try:
                import sqlite3

                con = sqlite3.connect(str(neutral_store))
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.close()
                shutil.copy2(neutral_store, run_dir / "team_nemo" / "shared" / "memory.sqlite")
                print("[run] memory store copied back to workspace")
            except Exception as e:
                print(f"[run] warning: memory store copy-back failed: {e}")

    result_path = run_dir / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        print(
            f"[run] finished: termination={result.get('termination_reason')} "
            f"levels={result.get('levels_completed')} "
            f"steps={result.get('total_steps')} "
            f"wall={result.get('wall_time_seconds')}s"
        )
    else:
        print(
            "[run] harness exited without result.json — check "
            f"{run_dir}/harness.log and {run_dir}/launcher.err"
        )

    if args.kill_tmux:
        tmux("kill-session", "-t", session)
        print(f"[run] tmux session {session} killed")
    else:
        _sk = f"-L {_TMUX_SOCKET} " if _TMUX_SOCKET else ""
        print(
            f"[run] tmux session {session} left running — attach to inspect, "
            f"or: tmux {_sk}kill-session -t {session}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
