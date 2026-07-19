# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration: the fleet deadline SIGTERM's the TUI and the terminal is restored.

Reproduces the exact user-facing failure: run_multi with the Textual TUI in a
pty, wall-clock deadline fires mid-game, the TUI is reaped — previously the
tty was left with mouse reporting on (bash then "executes" mouse events:
``-bash: 35: command not found``) and a Textual process that survived SIGTERM
kept run_multi blocked forever.

PASS = (1) the TUI enabled mouse/alt-screen modes (the hazard was exercised),
(2) after the LAST enable, every reset sequence appears in the pty stream,
(3) run_multi exits by itself, and (4) no orphan python processes remain.

Marked ``integration`` (needs the arc extra, gateway creds, offline game
files, and the progressive-learning venv for the Textual TUI):

    uv run pytest examples/arc_agi_3/tests/test_tui_terminal_restore.py -m integration
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EXAMPLE_DIR.parents[1]
PL_PY = REPO_ROOT / "progressive-learning" / ".venv" / "bin" / "python"

pytestmark = pytest.mark.integration

TINY_CONFIG = """\
name: tuirestore
operation_mode: offline
model: openai/openai/gpt-5.4
reasoning_effort: low
skill: grid-game-solver
variants: [mdfiles]
parallel: 1
max_turns: 50
max_env_steps: 500
max_wall_seconds: 45
effort_ladder: "600:medium"
games: [ls20]
"""

ENABLE_MARKERS = [b"\x1b[?1000h", b"\x1b[?1003h", b"\x1b[?1006h", b"\x1b[?1049h"]
RESET_MARKERS = [b"\x1b[?1003l", b"\x1b[?1006l", b"\x1b[?1049l", b"\x1b[?25h"]


def _have_creds() -> bool:
    from importlib import util

    if util.find_spec("arc_agi") is None:
        return False
    sys.path.insert(0, str(EXAMPLE_DIR))
    try:
        from arc_llm import has_llm_creds

        return has_llm_creds()
    except Exception:
        return False


@pytest.mark.skipif(not PL_PY.exists(), reason="progressive-learning venv (Textual TUI) absent")
@pytest.mark.skipif(not _have_creds(), reason="arc extra / gateway creds absent")
def test_deadline_sigterm_restores_terminal(tmp_path):
    cfg = tmp_path / "tiny.yaml"
    cfg.write_text(TINY_CONFIG)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 220, 0, 0))
    env = {**os.environ, "TERM": "xterm-256color", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, str(EXAMPLE_DIR / "run_multi.py"), "--config", str(cfg)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )
    os.close(slave)

    buf = bytearray()
    deadline = time.time() + 240
    try:
        while time.time() < deadline and proc.poll() is None:
            r, _, _ = select.select([master], [], [], 1.0)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
        # drain
        os.set_blocking(master, False)
        try:
            while True:
                chunk = os.read(master, 65536)
                if not chunk:
                    break
                buf.extend(chunk)
        except (OSError, BlockingIOError):
            pass
    finally:
        exited_on_its_own = proc.poll() is not None
        if not exited_on_its_own:
            os.killpg(proc.pid, 9)
        os.close(master)

    data = bytes(buf)
    assert exited_on_its_own, "run_multi did not exit after the wall-clock deadline"

    last_enable = max(data.rfind(m) for m in ENABLE_MARKERS)
    assert last_enable >= 0, "TUI never enabled mouse/alt-screen — hazard not exercised"
    for m in RESET_MARKERS:
        idx = data.rfind(m)
        assert idx >= 0, f"reset {m!r} never emitted"
        assert idx > last_enable, f"reset {m!r} not re-emitted after the last enable"

    # No orphans: nothing from this container may outlive run_multi.
    leftovers = subprocess.run(
        ["pgrep", "-af", "tuirestore"], capture_output=True, text=True
    ).stdout.strip()
    assert not leftovers, f"orphan processes left behind:\n{leftovers}"
