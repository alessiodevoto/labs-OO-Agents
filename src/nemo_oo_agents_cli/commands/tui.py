"""TUI command for nemo_oo_agents CLI.

Usage:
    nemo_oo_agents tui
    nemo_oo_agents tui --model gpt-4o
    nemo_oo_agents tui --working-dir /path/to/project
"""

import asyncio
import sys
from pathlib import Path

import click


@click.command()
@click.option(
    "--model",
    "-m",
    help="LLM model to use (overrides config default)",
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
def command(
    model: str | None,
    working_dir: str,
    mcp_file: str | None,
    no_splash: bool,
    skills_dir: tuple[str, ...],
    context_limit: int | None,
    orchestrator: bool,
    no_trace: bool,
):
    """Launch the Agent006 TUI (Text User Interface).

    Interactive REPL for chatting with agents, running commands, and managing
    skills and MCP servers.

    Examples:
        nemo_oo_agents tui
        nemo_oo_agents tui --model gpt-4o
        nemo_oo_agents tui --working-dir /path/to/project
        nemo_oo_agents tui --mcp-file .mcp.json
    """
    from nemo_oo_agents_cli.tui.config import Config
    from nemo_oo_agents_cli.tui.main import main as tui_main

    config = Config.load(
        model=model,
        working_dir=working_dir,
        mcp_file=Path(mcp_file) if mcp_file else None,
        no_splash=no_splash,
        skills_dir=list(skills_dir) if skills_dir else None,
        context_limit=context_limit,
        orchestrator=orchestrator,
        no_trace=no_trace,
    )

    try:
        asyncio.run(tui_main(config=config))
    except KeyboardInterrupt:
        sys.exit(0)
