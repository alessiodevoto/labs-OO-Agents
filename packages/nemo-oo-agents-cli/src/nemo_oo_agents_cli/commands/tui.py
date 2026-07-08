# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI command for nemo_oo_agents CLI.

Usage:
    nemo-oo tui
    nemo-oo tui --model gpt-4o
    nemo-oo tui --working-dir /path/to/project
"""

import asyncio
import sys
from pathlib import Path

import click

_RESUME_LAST = "__last__"


@click.command()
@click.option(
    "--model",
    "-m",
    help="LLM model to use (overrides config default)",
)
@click.option(
    "--agent",
    "agent_spec",
    default=None,
    metavar="MODULE:CLASS",
    help=(
        "Custom agent class instead of TUIAgent. "
        "Format: 'module.path:ClassName' or './file.py:ClassName'. "
        "Instantiated with llm=<configured llm>."
    ),
)
@click.option(
    "--working-dir",
    "-w",
    "-d",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=str),
    default=".",
    help="Working directory for bash commands",
)
@click.option(
    "--mcp-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=str),
    default=None,
    help="MCP config file (default: .mcp.json in cwd)",
)
@click.option(
    "--no-splash",
    is_flag=True,
    help="Skip the splash screen",
)
@click.option(
    "--skills-dir",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=str),
    help="Skills directory (can be specified multiple times)",
)
@click.option(
    "--context-limit",
    type=int,
    help="Context limit for summarization",
)
@click.option(
    "--orchestrator",
    is_flag=True,
    help="Use orchestrator mode",
)
@click.option(
    "--no-trace",
    is_flag=True,
    help="Disable tracing",
)
@click.option(
    "--vi",
    is_flag=True,
    help="Enable vi keybindings in the input prompt",
)
@click.option(
    "--python",
    is_flag=True,
    help="Show agent Python code execution panels",
)
@click.option(
    "--continue",
    "-c",
    "continue_session",
    is_flag=False,
    flag_value=_RESUME_LAST,
    default=None,
    help="Resume a session: -c (last session) or -c <short-hash>",
)
def command(
    model: str | None,
    agent_spec: str | None,
    working_dir: str,
    mcp_file: str | None,
    no_splash: bool,
    skills_dir: tuple[str, ...],
    context_limit: int | None,
    orchestrator: bool,
    no_trace: bool,
    vi: bool,
    python: bool,
    continue_session: str | None,
):
    """Launch the NeMo OO Agents TUI (Text User Interface).

    Interactive REPL for chatting with agents, running commands, and managing
    skills and MCP servers.

    Examples:
        nemo-oo tui
        nemo-oo tui --model gpt-4o
        nemo-oo tui --working-dir /path/to/project
        nemo-oo tui --mcp-file .mcp.json
        nemo-oo tui --agent ./my_agent.py:MyAgent
        nemo-oo tui --vi
    """
    from nemo_oo_agents_cli.tui.config import Config
    from nemo_oo_agents_cli.tui.main import main as tui_main

    config = Config.load(
        model=model,
        agent=agent_spec,
        working_dir=working_dir,
        mcp_file=Path(mcp_file) if mcp_file else None,
        no_splash=no_splash,
        skills_dir=list(skills_dir) if skills_dir else None,
        context_limit=context_limit,
        orchestrator=orchestrator,
        no_trace=no_trace,
        vi=vi,
        python=python,
    )

    continue_last = continue_session == _RESUME_LAST
    resume_session_id = (
        continue_session if continue_session and continue_session != _RESUME_LAST else None
    )

    try:
        asyncio.run(
            tui_main(
                config=config, continue_last=continue_last, resume_session_id=resume_session_id
            )
        )
    except KeyboardInterrupt:
        sys.exit(0)
