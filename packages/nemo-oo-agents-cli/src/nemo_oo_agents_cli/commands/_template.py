# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""<One-line description of what this command does.>

Copy this file as a starting point for a new nemo_oo_agents CLI command.

    1. Copy this file:     cp _template.py mycommand.py
    2. Rename `command` if needed (it stays as `command`)
    3. Add your logic
    4. Done — it's automatically discovered.

The filename becomes the subcommand name:
    mycommand.py  →  nemo-oo mycommand ...

To override the name, add at module level:
    NAME = "custom-name"

Usage:
    nemo-oo mycommand
    nemo-oo mycommand --verbose
"""

import click

# Optional: override the subcommand name (default: filename without .py)
# NAME = "custom-name"


# ---------------------------------------------------------------------------
# SIMPLE COMMAND — use this for leaf commands (no subcommands)
# ---------------------------------------------------------------------------


@click.command()
@click.argument("target", default="world")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output.")
def command(target: str, verbose: bool):
    """Short description shown in `nemo-oo --help`.

    TARGET is the thing to operate on.

    \b
    Examples:
        nemo-oo mycommand
        nemo-oo mycommand foo --verbose
    """
    click.echo(f"Hello, {target}!")
    if verbose:
        click.echo("(verbose mode)")


# ---------------------------------------------------------------------------
# GROUP COMMAND — uncomment this instead if you need subcommands
#                 (e.g. nemo-oo things list / nemo-oo things create)
# ---------------------------------------------------------------------------

# @click.group()
# def command():
#     """Manage things."""
#
#
# @command.command()
# def list():
#     """List all things."""
#     click.echo("thing-1")
#     click.echo("thing-2")
#
#
# @command.command()
# @click.argument("name")
# def create(name: str):
#     """Create a new thing."""
#     click.echo(f"Created: {name}")


# ---------------------------------------------------------------------------
# TIPS
# ---------------------------------------------------------------------------
#
# Shared utilities:
#     from nemo_oo_agents_cli._common import find_project_root, format_size
#
# Lazy imports (keep CLI startup fast — only import heavy deps inside handlers):
#     def command(...):
#         import pandas as pd    # ← lazy, not at module level
#
# Import the nemo_oo_agents framework (lazy — only when the command actually runs):
#     def command(...):
#         from nemo_oo_agents import Agent
