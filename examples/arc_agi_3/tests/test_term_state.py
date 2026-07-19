# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""term_state: terminal-mode restoration for the ARC viewers.

Fast, tty-free unit tests. The full breaking path (Textual TUI SIGTERM'd by
the fleet deadline watcher inside a pty) is exercised by the integration test
in test_tui_terminal_restore.py.
"""

from __future__ import annotations

import io
import signal
import subprocess
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_DIR))

import term_state  # noqa: E402

REQUIRED_RESETS = [
    b"\x1b[?1000l",  # basic mouse tracking off
    b"\x1b[?1002l",  # button-event tracking off
    b"\x1b[?1003l",  # any-event tracking off
    b"\x1b[?1006l",  # SGR mouse mode off
    b"\x1b[?2004l",  # bracketed paste off
    b"\x1b[?1049l",  # leave alternate screen
    b"\x1b[?25h",  # show cursor
]


class TestRestoreTerminal:
    def test_writes_every_reset_sequence(self):
        buf = io.BytesIO()
        term_state.restore_terminal(stream=buf, sane=False)
        data = buf.getvalue()
        for seq in REQUIRED_RESETS:
            assert seq in data, f"missing reset {seq!r}"

    def test_idempotent(self):
        buf = io.BytesIO()
        term_state.restore_terminal(stream=buf, sane=False)
        term_state.restore_terminal(stream=buf, sane=False)
        assert buf.getvalue() == term_state.RESTORE_SEQUENCES * 2

    def test_closed_stream_is_swallowed(self):
        buf = io.BytesIO()
        buf.close()
        term_state.restore_terminal(stream=buf, sane=False)  # must not raise

    def test_no_tty_is_noop(self, monkeypatch):
        # stdout not a tty and /dev/tty unopenable -> silently do nothing
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.setattr(
            "builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("no tty"))
        )
        term_state.restore_terminal()  # must not raise


class TestExitGuard:
    def test_sigterm_handler_restores_then_exits(self):
        """In a subprocess: install guard, raise SIGTERM at ourselves, verify the
        reset sequences hit stdout and the exit code is 128+SIGTERM."""
        code = (
            "import os, signal, sys; sys.path.insert(0, sys.argv[1]);"
            "import term_state;"
            # route restores to stdout regardless of tty-ness
            "term_state.restore_terminal = "
            "lambda *a, **k: (sys.stdout.buffer.write(term_state.RESTORE_SEQUENCES),"
            " sys.stdout.flush());"
            "term_state.install_exit_guard();"
            "os.kill(os.getpid(), signal.SIGTERM);"
            "import time; time.sleep(5); sys.exit(99)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, str(EXAMPLE_DIR)],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 128 + signal.SIGTERM
        for seq in REQUIRED_RESETS:
            assert seq in proc.stdout

    def test_atexit_restores_on_normal_exit(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import term_state;"
            "term_state.restore_terminal = "
            "lambda *a, **k: (sys.stdout.buffer.write(term_state.RESTORE_SEQUENCES),"
            " sys.stdout.flush());"
            "term_state.install_exit_guard()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, str(EXAMPLE_DIR)],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert term_state.RESTORE_SEQUENCES in proc.stdout

    def test_install_is_idempotent(self):
        term_state.install_exit_guard()
        term_state.install_exit_guard()  # second call must be a no-op, not raise
