# agent006 CLI — Adding New Commands

## Quick Start

```bash
cd src/agent006_cli/commands/
cp _template.py mycommand.py    # copy the template
# edit mycommand.py              # add your logic
agent006 mycommand              # it just works
```

That's it. No registration, no config files, no editing other files.

## How It Works

The CLI auto-discovers every `.py` file in `src/agent006_cli/commands/` at startup.
Files starting with `_` are ignored (private helpers, templates).

### The Contract

Your command module must export **one thing**: a module-level variable named `command`
that is a Click command or group.

```python
# commands/mycommand.py
import click

@click.command()
@click.argument("name")
def command(name: str):
    """One-line description shown in `agent006 --help`."""
    click.echo(f"Hello, {name}!")
```

The filename becomes the subcommand name: `mycommand.py` → `agent006 mycommand`.

### Override the Name

Add `NAME` at module level if the filename doesn't match what you want:

```python
NAME = "my-command"  # agent006 my-command (instead of agent006 mycommand)
```

## Patterns

### Simple Command (leaf, no subcommands)

```python
import click

@click.command()
@click.argument("target", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True)
def command(target: str, verbose: bool):
    """Do something to TARGET."""
    click.echo(f"Processing {target}")
```

### Group with Subcommands

For `agent006 things list` / `agent006 things create`:

```python
import click

@click.group()
def command():
    """Manage things."""

@command.command()
def list():
    """List all things."""
    click.echo("thing-1\nthing-2")

@command.command()
@click.argument("name")
def create(name: str):
    """Create a new thing."""
    click.echo(f"Created: {name}")
```

### Passthrough to Another CLI

For wrapping an existing tool without duplicating its options:

```python
import click

@click.command(
    add_help_option=False,
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    ),
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def command(args: tuple[str, ...]):
    """Run some-other-tool (all args forwarded)."""
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "some_other_tool", *args])
```

## Shared Utilities

Common helpers live in `src/agent006_cli/_common.py`:

```python
from agent006_cli._common import find_project_root, format_size, load_dotenv_into

root = find_project_root()           # Path to project root (where pyproject.toml is)
format_size(1_500_000)               # "1.4 MB"
load_dotenv_into(root / ".env", env) # Parse .env into a dict
```

## Performance Rule

**Keep imports lazy.** The CLI starts in ~0.3s because it doesn't import the
heavy `agent006` framework at module level. Only import heavy dependencies
inside your command handler function:

```python
@click.command()
def command():
    """Do something that needs the framework."""
    # These imports happen only when the command actually runs,
    # not when `agent006 --help` loads the CLI.
    from agent006 import Agent
    import pandas as pd
```

## File Layout

```
src/agent006_cli/
├── __init__.py              # Root CLI group + auto-discovery wiring
├── __main__.py              # python -m agent006_cli
├── _common.py               # Shared utilities
├── completion.py            # Shell completion (bash/zsh/fish)
├── AGENTS.md                # ← You are here
└── commands/
    ├── __init__.py          # Auto-discovery engine
    ├── _template.py         # Copy-paste starter for new commands
    ├── eval.py              # agent006 eval ...
    ├── sandbox.py           # agent006 sandbox ...
    ├── traces.py            # agent006 traces cleanup/list/stats
    └── start_dev.py         # agent006 start-dev
```

## Shell Completion

New commands get tab-completion automatically. To enable it:

```bash
agent006 completion install   # auto-detects your shell

# Or manually:
eval "$(_AGENT006_COMPLETE=zsh_source agent006)"   # zsh
eval "$(_AGENT006_COMPLETE=bash_source agent006)"  # bash
```
