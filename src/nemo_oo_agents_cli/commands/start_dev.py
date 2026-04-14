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
def command(port: int, host: str):
    """Start the unified trace + evaluation viewer."""
    import os

    from nemo_oo_agents_cli._common import USER_DATA_DIR

    # Set the viewer DB path before importing the viewer (it reads this at module level).
    db_path = USER_DATA_DIR / "traces.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TRACE_STORE_DB", str(db_path))

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
    click.echo()

    uvicorn.run(app, host=host, port=port, log_config=log_config)
