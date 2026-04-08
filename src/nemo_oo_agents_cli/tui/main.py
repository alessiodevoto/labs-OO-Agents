"""Main REPL loop for NeMo OO Agents TUI."""

from __future__ import annotations

import argparse
import asyncio
import signal
from typing import TYPE_CHECKING

from .agent import TUIAgent
from .commands import CommandHandler, CommandRegistry
from .config import Config, get_llm
from .console import TUIConsole
from .splash import show_splash

if TYPE_CHECKING:
    from nemo_oo_agents import Agent
    from nemo_oo_agents.tools import BashResult, BashTool


async def handle_bang_command(user_input: str, bash: BashTool) -> BashResult | None:
    """Handle ! prefix commands by running them directly through bash.

    Args:
        user_input: The user's input string starting with !
        bash: BashTool instance to run the command

    Returns:
        BashResult if a command was run, None if input was empty
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
        help="Context token limit for summarization (default: 100000). Set low to trigger summarization.",
    )
    parser.add_argument(
        "--orchestrator",
        action="store_true",
        help="Enable orchestrator mode (multi-phase workflow with intent classification)",
    )

    return parser.parse_args()


async def main(
    config: Config | None = None,
    agent: Agent | None = None,
) -> None:
    """Main entry point for the TUI.

    Args:
        config: Optional Config instance. If None, will parse args and load config.
        agent: Optional NeMo OO Agents agent. If None, a TUIAgent is created from
            ``config``. Any NeMo OO Agents subclass with a ``respond(message)`` method
            works. TUI features are auto-detected via ``hasattr``; agents that
            lack ``bash`` or ``get_summarization_status`` simply have those
            commands hidden.
    """
    if config is None:
        config = Config.load(**vars(parse_args()))

    # Initialize console
    console = TUIConsole()

    # Show splash screen
    if not config.no_splash:
        show_splash(console.console)

    if agent is None:
        # Enable tracing
        if not config.no_trace:
            try:
                from openinference_instrumentation_nemo_oo_agents import enable_tracing, exporters

                trace_dir = config.tui.trace_dir
                if trace_dir is not None:
                    trace_dir.mkdir(parents=True, exist_ok=True)
                    enable_tracing(exporters=[exporters.jsonl(trace_dir), exporters.journal()])
                else:
                    enable_tracing()
            except ImportError:
                console.print_warning(
                    "Tracing package not installed (openinference-instrumentation-nemo-oo-agents)"
                )
            except Exception as e:
                console.print_warning(f"Failed to enable tracing: {e}")

        # Get LLM client
        try:
            llm = get_llm(config)
        except Exception as e:
            console.print_error(f"Failed to initialize LLM: {e}")
            console.print_info("Using fake LLM client for testing")
            from unifiedllm import FakeLLMClient

            llm = FakeLLMClient()

        # Initialize default agent
        agent = TUIAgent(llm=llm, config=config.agent)

    # Initialize streaming display for real-time event visualization
    from .streaming_display import StreamingDisplay

    streaming_display = StreamingDisplay(console.console, tui_console=console)
    streaming_display.attach(agent)

    registry = CommandRegistry(
        config=config.tui,
        agent=agent,
        console=console,
        skills_dirs=config.tui.skills_dirs,
        mcp_file=config.tui.mcp_file,
        streaming_display=streaming_display,
    )
    commands = CommandHandler(registry=registry, console=console)
    console.init_input_handler(registry=registry)

    # Print startup info
    console.print_status("Type /help for commands, or just start chatting!")
    console.print_status("Type /exit to exit TUI")
    console.print_status("Tab: complete commands | Up/Down: history | Option+Enter: multiline")
    console.print_status("Ctrl+C: interrupt agent when running")
    console.print_status("Ctrl+D/Ctrl+C to exit TUI")

    # Show current model
    console.print_status(f"Model: {config.tui.default_model}")

    # Show summarization configuration (only for TUIAgent)
    if isinstance(agent, TUIAgent):
        console.print_status(
            f"History summarization: {config.agent.summarization.policy} "
            f"(limit: {config.agent.summarization.max_tokens:,} tokens)"
        )

    # Show bash sandbox status (only for agents with bash support)
    if hasattr(agent, "bash"):
        if agent.bash.sandbox_available:  # type: ignore[union-attr]
            console.print_status("SRT sandbox available. Use /sandbox to enable/disable sandboxing")
        else:
            console.print_status(
                "SRT sandbox not available. Bash commands will run unsandboxed. Install SRT for security."
            )

    console.console.print()  # Blank line before input

    # Main REPL loop
    try:
        while True:
            # Get user input (async for prompt_toolkit compatibility)
            user_input = await console.get_input("You: ")

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                result = await commands.handle(user_input)
                if result.exit:
                    break
                continue

            # Handle bang prefix: !command runs bash directly
            if user_input.startswith("!"):
                if not hasattr(agent, "bash"):
                    console.print_warning(
                        "Direct bash commands (!) require an agent with bash support."
                    )
                    continue
                result = await handle_bang_command(user_input, agent.bash)  # type: ignore[union-attr]
                if result:
                    if result.stdout:
                        console.console.print(result.stdout, end="")
                        if not result.stdout.endswith("\n"):
                            console.console.print()
                    if result.stderr:
                        console.print_error(result.stderr)
                continue

            # Regular message - send to agent
            # Clear previous streaming state and start spinner
            streaming_display.clear()
            console.start_spinner()

            # Wrap in a Task so Ctrl+C cancels only this call, not the whole REPL
            loop = asyncio.get_running_loop()
            task = asyncio.create_task(agent.respond(user_input))  # type: ignore[union-attr]
            try:
                loop.add_signal_handler(signal.SIGINT, task.cancel)
            except (NotImplementedError, OSError):
                pass  # Signal handlers not supported (e.g. Windows)

            try:
                await task
            except asyncio.CancelledError:
                console.print_warning("Interrupted execution by the user.")
                continue
            except Exception as e:
                console.print_error(f"Agent error: {e}")
                continue
            finally:
                console.stop_spinner()
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except (NotImplementedError, OSError):
                    pass

            # All agents are NeMo OO Agents agents — responses arrive via event_manager
            # (Message events), collected by StreamingDisplay.
            for msg in streaming_display.consume_messages():
                console.print_agent(msg)

    except (KeyboardInterrupt, EOFError):
        console.print_warning("Interrupted by the user. Exiting TUI...")

    # Detach streaming display
    streaming_display.detach()
