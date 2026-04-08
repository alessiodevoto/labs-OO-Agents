"""agent006 CLI — extensible command-line toolkit.

Usage:
    agent006 sandbox <agent_file>       # Run an agent in a sandbox
    agent006 eval <config.yaml>         # Run an eval-pipeline job
    agent006 start-dev                  # Start the viewer
    agent006 traces cleanup             # Cleanup traces
    agent006 completion install         # Set up shell completions

Adding new commands:
    Drop a .py file in agent006_cli/commands/ — see commands/_template.py

Shell completion:
    eval "$(_AGENT006_COMPLETE=bash_source agent006)"    # bash
    eval "$(_AGENT006_COMPLETE=zsh_source agent006)"     # zsh
"""

import click

from .commands import discover_commands
from .completion import completion

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(package_name="agent006")
def cli():
    """agent006 — Code-generating agent orchestration toolkit.

    Extensible CLI for running agents, evaluations, and trace management.
    Add new commands by dropping a .py file in agent006_cli/commands/.
    """


# -- Auto-discover and register all commands from commands/ -----------------
for _name, _cmd in discover_commands():
    cli.add_command(_cmd, name=_name)

# -- Built-in infrastructure commands (not in commands/ because they're meta) -
cli.add_command(completion)


def main():
    """Entry point for console_scripts."""
    cli()
