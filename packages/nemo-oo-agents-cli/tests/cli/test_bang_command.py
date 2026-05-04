# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test that TUI bang (!) commands work with ShellTools-based agents."""

from unittest.mock import AsyncMock, MagicMock

from nemo_oo_agents_cli.tui.session import Session


class TestBangCommand:
    """Verify that ! commands route through shell.bash, not self.bash."""

    async def test_bang_command_works_with_shell_agent(self):
        """An agent with self.shell (not self.bash) must handle ! commands."""
        # Create a mock agent with shell but no bash
        agent = MagicMock()
        del agent.bash  # ensure no bash attribute
        agent.shell = MagicMock()
        agent.shell.bash = AsyncMock(
            return_value=MagicMock(
                stdout="hello",
                stderr="",
                return_code=0,
            )
        )

        # Create a minimal session
        session = object.__new__(Session)
        session.agent = agent

        # The session should detect shell support
        assert hasattr(session.agent, "shell")
        assert not hasattr(session.agent, "bash")

        # Call _handle_bang — this should NOT show the warning
        frontend = AsyncMock()
        session.frontend = frontend

        await session._handle_bang("!echo hello")

        # Should have called shell.bash, not shown a warning
        agent.shell.bash.assert_called_once_with("echo hello")
