# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test utilities for TUI integration testing.

``TestFrontend`` implements the ``Frontend`` protocol with scripted inputs
and captured outputs so that ``Session.run()`` can be exercised in unit tests
without a real terminal.

Example usage::

    from nooa_tui.tui.testing import TestFrontend
    from nooa_tui.tui.session import Session

    frontend = TestFrontend(inputs=["/help", "/exit"])
    session = Session(frontend=frontend, agent=agent, config=config, registry=registry)
    await session.run()

    assert any(isinstance(o, HelpOutput) for o in frontend.outputs)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .output import Output


class TestFrontend:
    """A scripted :class:`Frontend` for use in tests.

    Pops inputs from a fixed list one at a time.  Raises ``EOFError`` when the
    list is exhausted, which terminates ``Session.run()`` naturally.  Every
    :class:`~.output.Output` object passed to :meth:`render` is appended to
    :attr:`outputs`.

    Args:
        inputs: Sequence of user-input strings to return in order.
    """

    __test__ = False  # prevent pytest from collecting this as a test class

    def __init__(self, inputs: list[str]) -> None:
        self._inputs: list[str] = list(inputs)
        self.outputs: list[Output] = []
        self._connected: bool = True

    async def render(self, output: "Output") -> None:
        self.outputs.append(output)

    async def get_input(
        self,
        prompt: str,
        completions: list[str] | None = None,
        default: str = "",
        bottom_toolbar=None,
    ) -> str:
        if not self._inputs:
            raise EOFError
        return self._inputs.pop(0)

    async def start_thinking(self, message: str = "thinking...") -> None:
        pass

    async def stop_thinking(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def open_editor(
        self, filename: str, content: str, language: str = "plaintext"
    ) -> str | None:
        return None

    def close(self) -> None:
        self._connected = False

    # ------------------------------------------------------------------
    # Convenience helpers for assertions
    # ------------------------------------------------------------------

    def outputs_of(self, cls: type) -> list:
        """Return all rendered outputs that are instances of *cls*."""
        return [o for o in self.outputs if isinstance(o, cls)]

    def text_contents(self) -> list[str]:
        """Return the ``content`` field of every ``TextOutput`` rendered."""
        from .output import TextOutput

        return [o.content for o in self.outputs if isinstance(o, TextOutput)]
