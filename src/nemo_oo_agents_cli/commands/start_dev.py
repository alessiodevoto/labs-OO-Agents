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
    try:
        from nemo_oo_agents_viewer.main import app
    except ImportError:
        click.secho(
            "Error: nemo-oo-agents-viewer is not installed.\nInstall it with:  uv add nemo_oo_agents[viewer]",
            fg="red",
            err=True,
        )
        raise SystemExit(1) from None

    import uvicorn

    logging.getLogger("uvicorn.access").addFilter(_AccessLogFilter())

    click.echo()
    click.secho("  NeMo OO Agents Viewer", fg="cyan", bold=True)
    click.echo(f"  URL:  http://localhost:{port}")
    click.echo()

    uvicorn.run(app, host=host, port=port)
