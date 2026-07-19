# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Terminal-state ownership for the ARC-AGI-3 viewers.

Full-screen viewers (the Textual multi-game TUI, rich Live) switch the shared
terminal into modes — xterm mouse reporting, alternate screen, hidden cursor —
that MUST be undone before a shell uses the tty again. When a viewer dies
without restoring them (SIGTERM from the fleet deadline watcher, a crash,
SIGKILL), the terminal keeps streaming mouse events to bash, which shows up as
``-bash: 35: command not found`` spam until the user resets the session.

Ownership is layered so every exit path is covered exactly once:

* **Viewer process** (``install_exit_guard()``): restores on interpreter exit
  (atexit — normal quit, exception) and on SIGTERM/SIGHUP (restore, then
  ``os._exit``). The process that flips the modes is responsible for them.
* **Parent orchestrator** (``restore_terminal()`` after reaping the viewer):
  covers what no child can — SIGKILL, hard crashes mid-write. Emitting the
  resets when the modes are already off is harmless, so the layers compose.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
from typing import BinaryIO

#: Every terminal mode our viewers may enable, reset in one write:
#: all xterm mouse-reporting variants, bracketed paste, alternate screen,
#: hidden cursor, application cursor keys / keypad.
RESTORE_SEQUENCES = (
    b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1005l\x1b[?1015l\x1b[?1006l"
    b"\x1b[?2004l"
    b"\x1b[?1049l"
    b"\x1b[?25h"
    b"\x1b[?1l\x1b>"
)


def restore_terminal(stream: BinaryIO | None = None, *, sane: bool = True) -> None:
    """Write the mode resets to the tty (idempotent; no-op without a tty).

    Args:
        stream: Explicit binary stream to write to (used by tests). When None,
            prefers stdout if it is a tty, else ``/dev/tty``.
        sane: Also run ``stty sane`` to restore echo/canonical input (only
            when operating on a real tty).
    """
    close_after = False
    if stream is None:
        if sys.stdout.isatty():
            stream = sys.stdout.buffer
        else:
            try:
                stream = open("/dev/tty", "wb", buffering=0)
                close_after = True
            except OSError:
                return
    try:
        stream.write(RESTORE_SEQUENCES)
        stream.flush()
    except (OSError, ValueError):
        return
    finally:
        if close_after:
            stream.close()
    if sane:
        try:
            subprocess.run(
                ["stty", "sane"],
                stdin=sys.stdin if sys.stdin.isatty() else None,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def install_exit_guard() -> None:
    """Make THIS process restore the terminal on every exit it can observe.

    * atexit — normal exit, unhandled exception, ``sys.exit`` (runs after the
      TUI framework's own cleanup; duplicate resets are harmless).
    * SIGTERM / SIGHUP — restore synchronously, then ``os._exit(128+sig)``.
      Textual's own SIGTERM handling queues a graceful shutdown on its event
      loop, which may never run when the loop is busy — restoring in the
      handler makes teardown independent of the loop. Deliberately does NOT
      touch SIGINT: frameworks handle Ctrl-C themselves, and atexit still
      covers that path.

    Call from the viewer's ``__main__`` before entering application mode.
    Idempotent per-process.
    """
    if getattr(install_exit_guard, "_installed", False):
        return
    install_exit_guard._installed = True  # type: ignore[attr-defined]

    atexit.register(restore_terminal)

    def _restore_and_exit(signum: int, _frame) -> None:
        restore_terminal()
        os._exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _restore_and_exit)
        except (ValueError, OSError):
            pass  # not the main thread / unsupported platform
