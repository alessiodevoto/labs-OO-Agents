"""Web terminal command for nemo_oo_agents CLI.

Launches an xterm.js browser terminal connected to a real PTY running the
NeMo OO Agents TUI.  A rich content side-channel lets the agent push Plotly
plots, HTML, and images to a collapsible browser panel while the terminal
session runs normally.
"""

import sys
from pathlib import Path

import click


@click.command()
@click.option("--model", "-m", help="LLM model to use (overrides config default)")
@click.option(
    "--agent",
    "agent_spec",
    default=None,
    metavar="MODULE:CLASS",
    help=(
        "Custom agent class instead of TUIAgent. "
        "Format: 'module.path:ClassName' or './file.py:ClassName'."
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
    "--skills-dir",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=str),
    help="Skills directory (can be specified multiple times)",
)
@click.option("--context-limit", type=int, help="Context limit for summarization")
@click.option("--orchestrator", is_flag=True, help="Use orchestrator mode")
@click.option("--no-trace", is_flag=True, help="Disable tracing")
@click.option("--vi", is_flag=True, help="Enable vi keybindings in the input prompt")
@click.option("--python", is_flag=True, help="Show agent Python code execution panels")
@click.option(
    "--continue",
    "-c",
    "continue_last",
    is_flag=True,
    help="Resume the most recent session on first connection",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind the web server to",
)
@click.option(
    "--port", "-p", default=8000, show_default=True, type=int, help="Port to listen on"
)
def command(
    model: str | None,
    agent_spec: str | None,
    working_dir: str,
    mcp_file: str | None,
    skills_dir: tuple[str, ...],
    context_limit: int | None,
    orchestrator: bool,
    no_trace: bool,
    vi: bool,
    python: bool,
    continue_last: bool,
    host: str,
    port: int,
):
    """Launch the NeMo OO Agents web terminal (xterm.js + rich side-channel).

    Opens an xterm.js terminal in your browser connected to a real PTY running
    the NeMo OO Agents TUI.  The agent can push Plotly plots, HTML, and images
    to a collapsible side panel using the WebPublisher tool
    (``self.web.plot(fig)``).

    Examples:
        nemo oo term
        nemo oo term --port 8080
        nemo oo term --model gpt-4o
        nemo oo term --agent ./my_agent.py:MyAgent
    """
    try:
        import uvicorn
        from uvicorn import Config, Server
    except ImportError:
        click.echo(
            "Web terminal requires uvicorn. Install with: uv add uvicorn[standard]",
            err=True,
        )
        sys.exit(1)

    try:
        import ptyprocess  # noqa: F401
    except ImportError:
        click.echo(
            "Web terminal requires ptyprocess. Install with: uv add ptyprocess",
            err=True,
        )
        sys.exit(1)

    # Build the TUI argv to spawn inside the PTY
    tui_argv = _build_tui_argv(
        model=model,
        agent_spec=agent_spec,
        working_dir=working_dir,
        mcp_file=mcp_file,
        skills_dir=skills_dir,
        context_limit=context_limit,
        orchestrator=orchestrator,
        no_trace=no_trace,
        vi=vi,
        python=python,
        continue_last=continue_last,
    )

    # Rich content endpoint URL that the agent will POST to
    rich_url = f"http://127.0.0.1:{port}/rich"
    env_extra = {"NEMO_RICH_URL": rich_url}

    from nemo_oo_agents_cli.web.pty_server import create_pty_app

    app, kill_all_procs = create_pty_app(tui_argv=tui_argv, env_extra=env_extra)

    click.echo(f"Starting NeMo OO Agents web terminal at http://{host}:{port}")
    click.echo(f"PTY command: {' '.join(tui_argv)}")

    import asyncio
    import signal
    import logging
    from contextlib import nullcontext

    _SHUTDOWN_TIMEOUT = 5  # seconds shown in countdown

    config = Config(app=app, host=host, port=port, log_level="warning",
                    timeout_graceful_shutdown=_SHUTDOWN_TIMEOUT + 1)
    server = Server(config)

    # We manage our own SIGINT/SIGTERM so we can show a countdown.
    server.capture_signals = nullcontext  # type: ignore[method-assign]

    # Suppress the "Cancel N running task(s), timeout graceful shutdown exceeded"
    # error that uvicorn logs when we force-close the long-lived PTY WebSocket.
    class _SuppressShutdownNoise(logging.Filter):
        _SUPPRESSED = ("timeout graceful shutdown", "CancelledError")

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            exc_str = str(record.exc_info or "")
            return not any(
                token in msg or token in exc_str for token in self._SUPPRESSED
            )

    _noise_filter = _SuppressShutdownNoise()
    for _ln in ("uvicorn.error", "uvicorn", "uvicorn.access"):
        _lg = logging.getLogger(_ln)
        _lg.addFilter(_noise_filter)
        for _h in _lg.handlers:
            _h.addFilter(_noise_filter)
    # Also apply to root logger handlers in case uvicorn propagates
    for _h in logging.getLogger().handlers:
        _h.addFilter(_noise_filter)

    async def _serve() -> None:
        loop = asyncio.get_running_loop()
        _countdown_started = False

        async def _countdown() -> None:
            for remaining in range(_SHUTDOWN_TIMEOUT, 0, -1):
                click.echo(f"\rShutting down... {remaining}s ", err=True, nl=False)
                await asyncio.sleep(1)
            click.echo("\rShutting down...           ", err=True, nl=False)
            server.force_exit = True

        def _on_signal() -> None:
            nonlocal _countdown_started
            if _countdown_started:
                return
            _countdown_started = True
            server.should_exit = True
            # Kill PTY processes immediately so WebSockets drain before uvicorn's
            # graceful-shutdown timer expires — this avoids the CancelledError that
            # occurs when uvicorn force-closes long-lived connections.
            kill_all_procs()
            click.echo("\nShutting down... ", err=True, nl=False)
            asyncio.ensure_future(_countdown())

        try:
            loop.add_signal_handler(signal.SIGINT, _on_signal)
            loop.add_signal_handler(signal.SIGTERM, _on_signal)
        except NotImplementedError:
            pass  # Windows

        await server.serve()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    click.echo("", err=True)  # newline after countdown
    sys.exit(0)


def _build_tui_argv(
    *,
    model: str | None,
    agent_spec: str | None,
    working_dir: str,
    mcp_file: str | None,
    skills_dir: tuple[str, ...],
    context_limit: int | None,
    orchestrator: bool,
    no_trace: bool,
    vi: bool,
    python: bool,
    continue_last: bool,
) -> list[str]:
    """Build the argv list for spawning ``nemo oo tui`` in the PTY."""
    import shutil

    # Find the nemo executable in the current environment
    nemo = shutil.which("nemo")
    if nemo is None:
        # Fallback: run as a module using the same Python interpreter
        nemo_argv = [sys.executable, "-m", "nemo_oo_agents_cli"]
    else:
        nemo_argv = [nemo]

    argv = nemo_argv + ["oo", "tui"]

    if model:
        argv += ["--model", model]
    if agent_spec:
        argv += ["--agent", agent_spec]
    if working_dir and working_dir != ".":
        argv += ["--working-dir", working_dir]
    if mcp_file:
        argv += ["--mcp-file", mcp_file]
    for sd in skills_dir:
        argv += ["--skills-dir", sd]
    if context_limit is not None:
        argv += ["--context-limit", str(context_limit)]
    if orchestrator:
        argv.append("--orchestrator")
    if no_trace:
        argv.append("--no-trace")
    if vi:
        argv.append("--vi")
    if python:
        argv.append("--python")
    if continue_last:
        argv.append("--continue")

    return argv
