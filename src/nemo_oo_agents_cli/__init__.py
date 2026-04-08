"""nemo_oo_agents CLI — extensible command-line toolkit.

Usage:
    nemo_oo_agents sandbox <agent_file>       # Run an agent in a sandbox
    nemo_oo_agents eval <config.yaml>         # Run an eval-pipeline job
    nemo_oo_agents start-dev                  # Start the viewer
    nemo_oo_agents traces cleanup             # Cleanup traces
    nemo_oo_agents completion install         # Set up shell completions

Adding new commands:
    Drop a .py file in nemo_oo_agents_cli/commands/ — see commands/_template.py

Shell completion:
    eval "$(_NEMO_OO_AGENTS_COMPLETE=bash_source nemo_oo_agents)"    # bash
    eval "$(_NEMO_OO_AGENTS_COMPLETE=zsh_source nemo_oo_agents)"     # zsh
"""

import click

from .commands import discover_commands
from .completion import completion

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(package_name="nemo_oo_agents")
def cli():
    """nemo_oo_agents — Code-generating agent orchestration toolkit.

    Extensible CLI for running agents, evaluations, and trace management.
    Add new commands by dropping a .py file in nemo_oo_agents_cli/commands/.
    """


# -- Auto-discover and register all commands from commands/ -----------------
for _name, _cmd in discover_commands():
    cli.add_command(_cmd, name=_name)

# -- Built-in infrastructure commands (not in commands/ because they're meta) -
cli.add_command(completion)


def main():
    """Entry point for console_scripts."""
    cli()
