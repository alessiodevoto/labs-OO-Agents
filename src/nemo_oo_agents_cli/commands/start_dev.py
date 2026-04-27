# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Start the development viewer.

Usage:
    nemo_oo_agents start-dev                  # Start viewer on :5001
    nemo_oo_agents start-dev --port 5002      # Custom port
"""

import logging

import click

NAME = "start-dev"

# Routes that are too noisy to log on every request.
_SUPPRESSED_PATHS = ("/v1/traces", "/api/trace", "/api/refresh")


class _AccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in _SUPPRESSED_PATHS)


@click.command()
@click.option("--port", "-p", type=int, default=5001, help="Port number (default: 5001).")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0).")
@click.option(
    "--db",
    "db_path_opt",
    type=click.Path(dir_okay=False),
    default=None,
    help="SQLite trace store path. Defaults to ~/Library/Application Support/nat/oo/traces.db "
    "(or $TRACE_STORE_DB if set). Pass an explicit path to run a second viewer "
    "side-by-side with the default one.",
)
def command(port: int, host: str, db_path_opt: str | None):
    """Start the unified trace + evaluation viewer."""
    import os
    from pathlib import Path

    from nemo_oo_agents.paths import get_user_dir

    # Resolve the DB path with --db winning, then $TRACE_STORE_DB, then the
    # user-dir default. Set TRACE_STORE_DB unconditionally so the viewer
    # module picks it up at import time (it reads the env var at the top).
    if db_path_opt:
        db_path = Path(db_path_opt).expanduser().resolve()
    elif "TRACE_STORE_DB" in os.environ:
        db_path = Path(os.environ["TRACE_STORE_DB"]).expanduser().resolve()
    else:
        db_path = get_user_dir("traces.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["TRACE_STORE_DB"] = str(db_path)

    try:
        from nemo_oo_agents_viewer.main import app
    except ImportError:
        click.secho(
            "Error: nemo-oo-agents-viewer is not installed.\nInstall it with:  uv add nemo_oo_agents[viewer]",
            fg="red",
            err=True,
        )
        raise SystemExit(1) from None

    import copy

    import uvicorn

    logging.getLogger("uvicorn.access").addFilter(_AccessLogFilter())

    # Include the application logger in uvicorn's log config so
    # nemo_oo_agents_viewer.* messages (info, warning, error) reach the console.
    # uvicorn.run() calls dictConfig() internally which reconfigures the
    # root logger, wiping any prior basicConfig() handler.  Adding our
    # logger directly to the config dict survives that reset.
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["loggers"]["nemo_oo_agents_viewer"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }

    click.echo()
    click.secho("  NeMo OO Agents Viewer", fg="cyan", bold=True)
    click.echo(f"  URL:  http://localhost:{port}")
    click.echo(f"  DB:   {db_path}")
    click.echo()

    uvicorn.run(app, host=host, port=port, log_config=log_config)
