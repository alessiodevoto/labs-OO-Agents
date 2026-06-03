# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression: /activity must not start the legacy rich.Live spinner.

Root cause of the persistent flicker + vanished ``self.message()`` output:
``TerminalFrontend._render_code_execution`` ended with
``self._console.start_spinner()``. In the prompt_toolkit world the console's
file is the block-queue ``_EmitStream`` (writes land in scrollback), and the
bottom-toolbar status line is the real thinking indicator. Starting a
``rich.live.Live`` spinner there made it repaint ``thinking...`` into the
scrollback continuously — the visible "swirl forever" — and its endless
writes buried ``self.message()`` output.

``/activity`` emits a ``CodeExecution`` (the suspend-point code block), which
is exactly why only it tripped this; ``/help`` and friends emit no
``CodeExecution`` and never started the Live spinner.
"""

from unittest.mock import MagicMock

import pytest
from nemo_oo_agents_cli.tui.frontend import TerminalFrontend
from nemo_oo_agents_cli.tui.output import CodeExecution


def _frontend() -> TerminalFrontend:
    cfg = MagicMock()
    cfg.tui.vi_mode = False
    return TerminalFrontend(cfg)


@pytest.mark.asyncio
async def test_code_execution_render_does_not_start_live_spinner():
    """Rendering a CodeExecution must not call start_spinner() (regression guard)."""
    fe = _frontend()
    fe._console.start_spinner = MagicMock()  # type: ignore[method-assign]

    await fe.render(
        CodeExecution(
            tool_call_id="t1",
            code="x = 1\n",
            stdout="",
            stderr="",
            error="",
            start_line=1,
        )
    )

    fe._console.start_spinner.assert_not_called()
