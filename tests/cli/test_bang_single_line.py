"""Test for issue #156: single-line !command output persists in TUI."""

import asyncio

import pytest

from .tui_app_harness import FakeAgent, TUIHarness


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
        await asyncio.sleep(0.05)
        output = h.capture_output()
        assert "/Volumes/dev" in output, (
            f"Single-line output should persist in output buffer, got: {output!r}"
        )


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

    from nemo_oo_agents_cli.tui.config import Config
    from nemo_oo_agents_cli.tui.frontend import TerminalFrontend
    from nemo_oo_agents_cli.tui.output import BashOutput
    from rich.console import Console
    from nemo_oo_agents_cli.tui.theme import CATPPUCCIN_THEME

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
    assert emitted[0].endswith("\n"), (
        f"Emitted block should end with newline, got: {emitted[0]!r}"
    )

