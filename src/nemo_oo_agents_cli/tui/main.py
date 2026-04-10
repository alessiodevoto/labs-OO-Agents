# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Main entry point for NeMo OO Agents TUI (terminal frontend).

Thin wrapper around the shared bootstrap.  Creates a ``TerminalFrontend``,
calls ``bootstrap()``, wires them together, and runs the session.

The ``main()`` coroutine keeps its original signature so that callers like
``examples/tools_agent_tui/example.py`` continue to work unchanged::

    await main(config=config, agent=agent)
"""

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nemo_oo_agents import Agent

    from .config import Config


async def handle_bang_command(user_input: str, bash) -> object:
    """Run a !command through bash. Returns BashResult or None for empty/whitespace input.

    Backward-compat wrapper kept for external callers and tests.
    """
    cmd = user_input[1:].strip()
    if not cmd:
        return None
    return await bash.run(cmd)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="NeMo OO Agents TUI - A beautiful terminal interface for NeMo OO Agents",
        prog="python -m tui",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=None,
        help="LLM model from registry (e.g., aws/anthropic/bedrock-claude-sonnet-4-5-v1)",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        metavar="MODULE:CLASS",
        help=(
            "Custom agent class to use instead of TUIAgent. "
            "Format: 'module.path:ClassName' or './file.py:ClassName'. "
            "The class is instantiated with llm=<configured llm>."
        ),
    )
    parser.add_argument(
        "--no-splash",
        action="store_true",
        help="Skip the splash screen",
    )
    parser.add_argument(
        "--working-dir",
        "-w",
        type=str,
        default=".",
        help="Working directory for bash commands",
    )
    parser.add_argument(
        "--mcp-file",
        type=str,
        default=None,
        help="MCP file to use (default: .mcp.json in cwd)",
    )
    parser.add_argument(
        "--skills-dir",
        type=str,
        action="append",
        help="Additional skills directory (can be specified multiple times)",
    )
    parser.add_argument(
        "--trace",
        type=str,
        nargs="?",
        const="traces/tui",
        default=None,
        metavar="DIR",
        help="Also write traces to DIR as JSONL files (default: traces/tui when flag is given)",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Disable tracing",
    )
    parser.add_argument(
        "--context-limit",
        "-c",
        type=int,
        default=None,
        help="Context token limit for summarization (default: 100000).",
    )
    parser.add_argument(
        "--orchestrator",
        action="store_true",
        help="Enable orchestrator mode (multi-phase workflow with intent classification)",
    )
    parser.add_argument(
        "--vi",
        action="store_true",
        help="Enable vi keybindings in the input prompt",
    )

    return parser.parse_args()


async def main(
    config: "Config | None" = None,
    agent: "Agent | None" = None,
    continue_last: bool = False,
) -> None:
    """Main entry point for the TUI.

    Args:
        config: Optional Config instance.  If None, parse args and load config.
        agent: Optional NeMo OO Agents agent.  If None, a TUIAgent (or custom class
               from ``config.tui.agent_spec``) is created from ``config``.
               Any NeMo OO Agents subclass with a ``respond(message)`` method works.
    """
    from .bootstrap import bootstrap, build_registry, build_session, build_startup_info
    from .config import Config
    from .frontend import TerminalFrontend
    from .output import TextOutput, _RichReplayPayload
    from .session_manager import SESSIONS_DIR, build_resume_outputs
    from .splash import show_splash

    if config is None:
        config = Config.load(**vars(parse_args()))

    # Terminal-specific: create frontend and splash screen
    frontend = TerminalFrontend(config)
    if not config.no_splash:
        show_splash(frontend.raw_console)

    # Shared bootstrap: tracing, LLM, storage, agent, session manager
    result = await bootstrap(
        config,
        continue_last=continue_last,
        agent=agent,
    )

    # Render bootstrap messages (errors, warnings, info)
    for msg in result.messages:
        await frontend.render(msg)

    # Startup info panel
    await frontend.render(build_startup_info(result))

    # Show resumed session history (interleaved with any rich content)
    if result.resumed and result.session_id is not None:
        import os as _os

        _in_nemo_term = bool(_os.environ.get("NEMO_RICH_URL"))
        _db_path = SESSIONS_DIR / f"{result.session_id}.db"
        _resume_outputs = build_resume_outputs(
            _db_path, result.session_id, in_nemo_term=_in_nemo_term
        )
        if _resume_outputs:
            _rich_url = _os.environ.get("NEMO_RICH_URL") if _in_nemo_term else None
            for _item in _resume_outputs:
                if isinstance(_item, _RichReplayPayload):
                    if _rich_url:
                        try:
                            import httpx as _httpx

                            _httpx.post(
                                _rich_url, json={**_item.payload, "_replay": True}, timeout=5.0
                            )
                        except Exception:
                            pass
                else:
                    await frontend.render(_item)
            await frontend.render(TextOutput(f"Session {result.session_id[:8]} resumed.", "status"))
        else:
            await frontend.render(TextOutput("No previous session with turns found.", "info"))
    elif continue_last:
        await frontend.render(TextOutput("No previous session with turns found.", "info"))

    # Wire frontend → registry → session
    registry = build_registry(result, frontend)
    frontend.init_input(registry)  # terminal-specific: prompt_toolkit completions
    session = build_session(result, frontend, registry)
    await session.run()
