"""NeMo OO Agents CLI — extensible command-line toolkit.

Usage:
    nemo-oo sandbox <agent_file>       # Run an agent in a sandbox
    nemo-oo eval <config.yaml>         # Run an eval-pipeline job
    nemo-oo start-dev                  # Start the viewer
    nemo-oo traces cleanup             # Cleanup traces
    nemo-oo completion install         # Set up shell completions

Adding new commands:
    Drop a .py file in nemo_oo_agents_cli/commands/ — see commands/_template.py

Shell completion:
    eval "$(_NEMO_OO_AGENTS_COMPLETE=bash_source nemo-oo)"    # bash
    eval "$(_NEMO_OO_AGENTS_COMPLETE=zsh_source nemo-oo)"     # zsh
"""

import click

from .commands import discover_commands
from .completion import completion

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(package_name="nemo-oo-agents")
def oo():
    """NeMo OO Agents — agent orchestration toolkit.

    Extensible CLI for running agents, evaluations, and trace management.
    Add new commands by dropping a .py file in nemo_oo_agents_cli/commands/.
    """


# -- Auto-discover and register all commands from commands/ -----------------
for _name, _cmd in discover_commands():
    oo.add_command(_cmd, name=_name)

# -- Built-in infrastructure commands (not in commands/ because they're meta) -
oo.add_command(completion)

# Keep cli as alias for backward compat
cli = oo


def main():
    """Entry point for console_scripts (standalone: nemo-oo, future: nemo oo)."""
    oo()
