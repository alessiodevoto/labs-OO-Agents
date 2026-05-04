# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Forward stray ``sys.stdout`` / ``sys.stderr`` writes into the TUI scrollback.

Libraries (aiohttp, litellm, warnings, third-party logs, etc.) sometimes
write directly to ``sys.stdout`` / ``sys.stderr``. Under
prompt_toolkit those raw writes interleave with the TUI's cursor
commands and corrupt the display.

``_StrayStreamForwarder`` replaces the real stream with a line-buffered
wrapper that routes each complete line through ``emit_block`` — the
same pipeline the TUI uses for code previews and agent messages — so
the output lands in the transcript above the prompt instead of over
top of it. Styled with a small prefix (``·`` dim for stdout, ``!`` red
for stderr) so the user can tell it apart from first-class output.

Installation order matters: wrap sys.stdout BEFORE the framework
installs its own ``ContextVarStream`` wrapper. That way the framework's
wrapper's ``_original`` becomes our forwarder, and agent-cell stdout
capture still works — the framework only forwards when no contextvar
buffer is set, i.e. exactly the stray-write case we care about.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import IO, Any


class _StrayStreamForwarder:
    """Thread-safe line-buffered forwarder for stray stdout/stderr writes.

    ``write()`` accumulates partial lines in an internal buffer and flushes
    each complete line through ``emit_block`` as an ANSI-styled chunk.
    Non-write attribute access (``encoding``, ``fileno``, etc.) falls
    through to the wrapped stream so the object passes duck-type checks
    elsewhere in the process (e.g. ``sys.stdout.isatty()``).
    """

    def __init__(
        self,
        original: IO[str],
        emit_block: Callable[[str], None],
        *,
        prefix: str,
        ansi_color: str,
    ) -> None:
        self._original = original
        self._emit_block = emit_block
        self._prefix = prefix
        self._ansi_color = ansi_color
        self._pending = ""
        self._lock = threading.Lock()

    # ── IO protocol ────────────────────────────────────────────────────

    def write(self, data: str) -> int:
        """Accept a write; emit each newline-terminated line via ``emit_block``.

        Returns the number of characters accepted (following ``io.TextIOBase``).
        """
        if not data:
            return 0
        with self._lock:
            buf = self._pending + data
            lines = buf.split("\n")
            # Everything before the final split is a complete line; the last
            # element is whatever came after the last ``\n`` (possibly empty).
            self._pending = lines[-1]
            complete = lines[:-1]
        for line in complete:
            self._emit_line(line)
        return len(data)

    def writelines(self, lines: list[str]) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        """Flush any buffered partial line.

        Libraries that write ``"foo"`` then ``flush()`` without a trailing
        newline still want ``foo`` visible; flushing treats the pending
        buffer as a complete line.
        """
        with self._lock:
            pending = self._pending
            self._pending = ""
        if pending:
            self._emit_line(pending)

    # ── internals ──────────────────────────────────────────────────────

    def _emit_line(self, line: str) -> None:
        """Wrap a single line in the configured ANSI style and emit it."""
        # Always terminate with a newline so the TUI's block consumer
        # places the next block on a fresh line. Skip purely empty lines
        # to avoid runs of blank chunks from adjacent "\n\n" sequences.
        if not line and not self._prefix:
            return
        styled = f"\x1b[{self._ansi_color}m{self._prefix}{line}\x1b[0m\n"
        try:
            self._emit_block(styled)
        except Exception:
            # If emit_block blows up for any reason, fall through to the
            # original stream so the write isn't silently lost.
            try:
                self._original.write(styled)
                self._original.flush()
            except Exception:
                pass

    # ── duck-type compatibility ────────────────────────────────────────

    def isatty(self) -> bool:
        """Report non-tty so libraries don't emit ANSI escape sequences
        (which would otherwise show up as literal characters in the line
        the forwarder emits)."""
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def install_stray_stream_capture(emit_block: Callable[[str], None]) -> Callable[[], None]:
    """Install forwarders on ``sys.stdout`` and ``sys.stderr``.

    Returns a zero-arg ``uninstall`` callable that restores whatever
    streams were in place *before* this call. Call it in the TUI's
    shutdown path so post-exit prints go back to the real terminal.

    Must be invoked on the main thread before any agent code runs —
    otherwise the framework's ``ContextVarStream`` will be layered
    underneath our wrapper and agent-cell stdout capture will break.
    """
    import sys

    prior_stdout = sys.stdout
    prior_stderr = sys.stderr

    sys.stdout = _StrayStreamForwarder(  # type: ignore[assignment]
        prior_stdout,
        emit_block,
        prefix="· ",
        ansi_color="2",  # dim
    )
    sys.stderr = _StrayStreamForwarder(  # type: ignore[assignment]
        prior_stderr,
        emit_block,
        prefix="! ",
        ansi_color="31",  # red
    )

    def uninstall() -> None:
        # Flush any partial lines before restoring so they aren't lost.
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, _StrayStreamForwarder):
                try:
                    stream.flush()
                except Exception:
                    pass
        sys.stdout = prior_stdout
        sys.stderr = prior_stderr

    return uninstall
