#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Drive the NeMo OO Agents TUI via a pty and snapshot its rendered screen.

Manual smoke-test tool — spawns the TUI in a pty, writes keystrokes on
stdin, captures output through a ``pyte`` VT100 emulator, dumps screen
snapshots on CAPTURE lines. Used during plan-C development to A/B old
vs new TUI behaviour without watching a real terminal.

Not invoked by pytest; kept under ``tests/cli/`` as a dev diagnostic.

Example::

    tests/cli/tui_drive.py -- uv run nemo oo tui <<'EOF'
    SLEEP 7
    TYPE /help
    KEY enter
    SLEEP 1
    CAPTURE
    KEY c-d
    EOF

Script commands (one per line, case-insensitive):

    TYPE <text>       — write literal text to the tty
    KEY <name>        — send one named key (see KEY_SEQS below)
    SLEEP <seconds>   — wait (decimals allowed)
    CAPTURE           — dump the current screen to stdout and continue
    EXPECT <needle>   — poll for up to 5s, fail if needle never appears
    WAIT_PROMPT       — wait until the ``❯ `` prompt is visible
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import time
from pathlib import Path

import ptyprocess  # type: ignore[import-untyped]
import pyte  # type: ignore[import-untyped]

KEY_SEQS: dict[str, bytes] = {
    "enter": b"\r",
    "escape": b"\x1b",
    "esc": b"\x1b",
    "tab": b"\t",
    "backspace": b"\x7f",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "c-c": b"\x03",
    "c-d": b"\x04",
    "c-j": b"\n",
    "s-enter": b"\x1b\r",
}


def _drain(proc: ptyprocess.PtyProcess, stream: pyte.ByteStream, deadline: float) -> None:
    """Consume bytes until ``deadline`` is reached, feeding the pyte stream.

    Uses ``select`` so we never block past the deadline even when the
    child has gone quiet. ptyprocess.read() is blocking by default.
    """
    fd = proc.fd
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            r, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        except (OSError, ValueError):
            return
        if not r:
            continue
        try:
            data = os.read(fd, 4096)
        except (EOFError, OSError):
            return
        if not data:
            return
        stream.feed(data)


def _render(screen: pyte.Screen) -> str:
    """Return the terminal screen as plain text, stripping empty bottom lines."""
    lines = screen.display
    # Trim trailing blanks for a tighter snapshot
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


def _screen_contains(screen: pyte.Screen, needle: str) -> bool:
    return needle in "\n".join(screen.display)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--script", type=Path, help="Script file (default: stdin)")
    parser.add_argument(
        "--quiet", action="store_true", help="Only print CAPTURE outputs (no status)"
    )
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run (after --)")
    args = parser.parse_args()

    if not args.cmd:
        print("error: supply a command after --", file=sys.stderr)
        return 2
    if args.cmd[0] == "--":
        args.cmd = args.cmd[1:]

    script_lines: list[str]
    if args.script:
        script_lines = args.script.read_text().splitlines()
    else:
        script_lines = sys.stdin.read().splitlines()

    def log(msg: str) -> None:
        if not args.quiet:
            print(f"[driver] {msg}", file=sys.stderr, flush=True)

    log(f"spawning: {' '.join(args.cmd)}  ({args.cols}x{args.rows})")
    env = os.environ.copy()
    # Force ANSI-capable terminal emulation regardless of host terminal.
    env.setdefault("TERM", "xterm-256color")

    proc = ptyprocess.PtyProcess.spawn(args.cmd, dimensions=(args.rows, args.cols), env=env)
    screen = pyte.Screen(args.cols, args.rows)
    stream = pyte.ByteStream(screen)

    try:
        for raw in script_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cmd, _, rest = line.partition(" ")
            cmd_l = cmd.lower()

            if cmd_l == "type":
                log(f"TYPE {rest!r}")
                proc.write(rest.encode("utf-8"))
                # Let the TUI process typed chars before the next action.
                _drain(proc, stream, time.monotonic() + 0.05)
            elif cmd_l == "key":
                seq = KEY_SEQS.get(rest.lower())
                if seq is None:
                    print(f"[driver] unknown key {rest!r}", file=sys.stderr)
                    return 2
                log(f"KEY {rest.lower()}")
                proc.write(seq)
                _drain(proc, stream, time.monotonic() + 0.1)
            elif cmd_l == "sleep":
                if not rest.strip():
                    print("[driver] SLEEP requires a duration", file=sys.stderr)
                    return 2
                try:
                    secs = float(rest)
                except ValueError:
                    print(f"[driver] SLEEP duration not a number: {rest!r}", file=sys.stderr)
                    return 2
                log(f"SLEEP {secs}")
                _drain(proc, stream, time.monotonic() + secs)
            elif cmd_l == "capture":
                log("CAPTURE")
                _drain(proc, stream, time.monotonic() + 0.1)
                sys.stdout.write("\n=== screen ===\n")
                sys.stdout.write(_render(screen))
                sys.stdout.write("\n=== /screen ===\n")
                sys.stdout.flush()
            elif cmd_l == "expect":
                deadline = time.monotonic() + 5.0
                log(f"EXPECT {rest!r}")
                while time.monotonic() < deadline:
                    _drain(proc, stream, time.monotonic() + 0.1)
                    if _screen_contains(screen, rest):
                        break
                else:
                    print(f"[driver] EXPECT timeout: {rest!r} not on screen", file=sys.stderr)
                    sys.stdout.write(_render(screen))
                    return 1
            elif cmd_l == "wait_prompt":
                deadline = time.monotonic() + 10.0
                log("WAIT_PROMPT")
                while time.monotonic() < deadline:
                    _drain(proc, stream, time.monotonic() + 0.1)
                    if _screen_contains(screen, "❯"):
                        break
                else:
                    print("[driver] WAIT_PROMPT timeout", file=sys.stderr)
                    sys.stdout.write(_render(screen))
                    return 1
            else:
                print(f"[driver] unknown script command: {cmd!r}", file=sys.stderr)
                return 2
    finally:
        try:
            if proc.isalive():
                proc.sendcontrol("d")  # Ctrl+D → clean exit
                _drain(proc, stream, time.monotonic() + 0.5)
                if proc.isalive():
                    proc.terminate(force=True)
        except Exception as exc:
            if not args.quiet:
                print(f"[driver] cleanup error: {exc}", file=sys.stderr)
        proc.close(force=True) if hasattr(proc, "close") else None

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
