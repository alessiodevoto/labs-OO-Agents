"""<One-line description of what this command does.>

Copy this file as a starting point for a new agent006 CLI command.

    1. Copy this file:     cp _template.py mycommand.py
    2. Rename `command` if needed (it stays as `command`)
    3. Add your logic
    4. Done — it's automatically discovered.

The filename becomes the subcommand name:
    mycommand.py  →  agent006 mycommand ...

To override the name, add at module level:
    NAME = "custom-name"

Usage:
    agent006 mycommand
    agent006 mycommand --verbose
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
    """Short description shown in `agent006 --help`.

    TARGET is the thing to operate on.

    \b
    Examples:
        agent006 mycommand
        agent006 mycommand foo --verbose
    """
    click.echo(f"Hello, {target}!")
    if verbose:
        click.echo("(verbose mode)")


# ---------------------------------------------------------------------------
# GROUP COMMAND — uncomment this instead if you need subcommands
#                 (e.g. agent006 things list / agent006 things create)
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
#     from agent006_cli._common import find_project_root, format_size
#
# Lazy imports (keep CLI startup fast — only import heavy deps inside handlers):
#     def command(...):
#         import pandas as pd    # ← lazy, not at module level
#
# Import the agent006 framework (lazy — only when the command actually runs):
#     def command(...):
#         from agent006 import Agent
