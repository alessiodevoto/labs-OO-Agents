# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test for issue #156: single-line !command output persists in TUI."""

import pytest

from .tui_app_harness import TUIHarness

pytestmark = pytest.mark.asyncio


async def test_single_line_bang_output_persists_in_output_buffer():
    """Issue #156: single-line !command output (e.g. !pwd) must remain
    visible in the scrollback.

    The output should land in output_buffer.text after going through
    emit_block.
    """
    async with TUIHarness() as h:
        # Push a single-line block directly through emit_block
        h.app.emit_block("/Volumes/dev\n")
        # emit_block on-thread path updates output_buffer synchronously,
        # but yield to let any queued callbacks run.
        await h.wait_output_contains("/Volumes/dev")


async def test_render_bash_single_line_emits_one_block():
    """_render_bash must produce exactly ONE emit_block call for single-line
    output — not two (text + bare newline) which causes a race with
    prompt_toolkit's run_in_terminal repaint.

    This test catches the _EmitStream batching bug: console.print(text, end="")
    followed by a conditional console.print() creates two separate flushes.
    """
    emitted: list[str] = []

    class _TrackingStream:
        """Mimics _EmitStream but tracks flushes."""

        def __init__(self):
            self._buf: list[str] = []

        def write(self, text):
            if text:
                self._buf.append(text)
            return len(text)

        def flush(self):
            if self._buf:
                chunk = "".join(self._buf)
                self._buf.clear()
                emitted.append(chunk)

        def isatty(self):
            return True

    from nooa_tui.tui.config import Config
    from nooa_tui.tui.frontend import TerminalFrontend
    from nooa_tui.tui.output import BashOutput
    from nooa_tui.tui.theme import CATPPUCCIN_THEME
    from rich.console import Console

    # Build a TerminalFrontend with our tracking stream
    config = Config()
    frontend = TerminalFrontend(config)
    tracking = _TrackingStream()
    frontend._console.replace_console(
        Console(
            file=tracking,
            force_terminal=True,
            color_system="256",
            width=120,
            theme=CATPPUCCIN_THEME,
        )
    )

    # Case 1: stdout WITH trailing newline — should produce exactly 1 emit
    emitted.clear()
    await frontend.render(BashOutput(stdout="hello\n", stderr="", return_code=0))
    assert len(emitted) == 1, (
        f"Expected 1 emit for stdout ending with newline, got {len(emitted)}: {emitted!r}"
    )

    # Case 2: stdout WITHOUT trailing newline — should ALSO produce exactly 1 emit
    emitted.clear()
    await frontend.render(BashOutput(stdout="hello", stderr="", return_code=0))
    assert len(emitted) == 1, (
        f"Expected 1 emit for single-line output (even without trailing newline), "
        f"got {len(emitted)}: {emitted!r}"
    )
    # The single emit should end with a newline
    assert emitted[0].endswith("\n"), f"Emitted block should end with newline, got: {emitted[0]!r}"
