# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test that TUI bang (!) commands work with ShellTools-based agents."""

from unittest.mock import AsyncMock, MagicMock

from nemo_oo_agents_cli.tui.session import Session


class TestBangCommand:
    """Verify that ! commands route through the TUI's own ShellTools."""

    async def test_bang_command_works_with_shell_agent(self):
        """An agent with self.shell must handle ! commands via TUI-owned shell."""
        # Create a mock agent with shell but no bash
        agent = MagicMock()
        del agent.bash  # ensure no bash attribute
        agent.shell = MagicMock()
        agent.shell.cwd = "/tmp"

        # Create a minimal session with a pre-set _bang_shell mock
        session = object.__new__(Session)
        session.agent = agent
        bang_shell = MagicMock()
        bang_shell.run = AsyncMock(
            return_value=MagicMock(
                stdout="hello",
                stderr="",
                returncode=0,
            )
        )
        session._bang_shell = bang_shell

        # The session should detect shell support
        assert hasattr(session.agent, "shell")
        assert not hasattr(session.agent, "bash")

        # Call _handle_bang — this should NOT show the warning
        frontend = AsyncMock()
        session.frontend = frontend

        await session._handle_bang("!echo hello")

        # Should have called the TUI's own bang shell, not the agent's
        bang_shell.run.assert_called_once_with("echo hello")
        agent.shell.run.assert_not_called()
