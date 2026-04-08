"""Sandbox command for nemo_oo_agents CLI.

``nemo_oo_agents sandbox`` is an opinionated zero-config wrapper around
``openshell sandbox``:

    nemo_oo_agents sandbox -- python agent.py              # run a script
    nemo_oo_agents sandbox -- nemo_oo_agents tui                 # launch the TUI
    nemo_oo_agents sandbox -- bash                         # open a shell
    nemo_oo_agents sandbox --upload src:ro -- python a.py  # read-only mount
    nemo_oo_agents sandbox --env HF_TOKEN=x -- python a.py # inject credentials
    nemo_oo_agents sandbox --allow-domain api.x.com -- ... # allow extra domain

Always requires a ``pyproject.toml`` in the current directory.  Before
running the command the sandbox:

1. Uploads the current directory by default (``--upload PATH[:ro|:rw]``
   to specify explicit paths; ``pyproject.toml`` always included)
2. Installs dependencies via ``uv sync``

``--env KEY=VALUE`` injects environment variables via a short-lived
credential provider that is created and deleted automatically.

``--allow-domain HOST`` extends the network policy to allow outbound
HTTPS to additional hosts.

For anything beyond this (port forwarding, long-running tasks,
connecting to existing sandboxes, etc.) use ``openshell`` directly.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import click
import yaml

_POLICY = Path(__file__).parent / "sandbox-policy.yaml"
_PROVIDER = "env-vars"
_PREFIX = "nemo_oo_agents-"


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _check_openshell() -> None:
    if not shutil.which("openshell"):
        raise click.ClickException("openshell not found. Install with: uv add nemo_oo_agents[sandbox]")


def _ensure_provider() -> None:
    """Create the openshell credential provider if it doesn't exist yet."""
    result = subprocess.run(
        ["openshell", "provider", "list", "--names"], capture_output=True, text=True
    )
    if _PROVIDER in result.stdout.splitlines():
        return
    subprocess.run(
        [
            "openshell",
            "provider",
            "create",
            "--name",
            _PROVIDER,
            "--type",
            "generic",
            "--credential",
            f"NVIDIA_INTERNAL_API_KEY={os.getenv('NVIDIA_INTERNAL_API_KEY', '')}",
            "--credential",
            f"OPENAI_API_KEY={os.getenv('OPENAI_API_KEY', '')}",
            "--credential",
            f"NVIDIA_API_KEY={os.getenv('NVIDIA_API_KEY', '')}",
        ],
        check=True,
    )


def _sandbox_dest(local: str) -> str:
    """Infer the sandbox destination path for an uploaded local path.

    openshell uploads '.' to /sandbox and named paths to /sandbox/<name>.
    """
    return "/sandbox" if local in (".", "./") else f"/sandbox/{Path(local).name}"


def _parse_upload(raw: str) -> tuple[str, bool]:
    """Parse an --upload value into (local_path, read_only).

    Accepted formats:
        src           → ("src", False)
        src:ro        → ("src", True)
        src:rw        → ("src", False)
    """
    if raw.endswith(":ro"):
        return raw[:-3], True
    if raw.endswith(":rw"):
        return raw[:-3], False
    return raw, False


def _write_policy(
    domains: tuple[str, ...] = (),
    readonly_paths: list[str] | None = None,
) -> Path:
    """Write a patched policy YAML and return its path.

    Extends the bundled policy with optional extra network domains and
    optional read-only sandbox paths.
    """
    policy = yaml.safe_load(_POLICY.read_text())

    if domains:
        policy.setdefault("network_policies", {})["user_domains"] = {
            "name": "user-domains",
            "endpoints": [{"host": h, "port": 443} for h in domains],
            "binaries": [
                {"path": "/sandbox/.venv/bin/python"},
                {"path": "/sandbox/.venv/bin/python3"},
                {"path": "/usr/local/bin/python"},
                {"path": "/usr/local/bin/python3"},
                {"path": "/usr/local/bin/uv"},
                {"path": "/sandbox/.uv/python/**"},
            ],
        }

    if readonly_paths:
        fs = policy.setdefault("filesystem_policy", {})
        existing = fs.get("read_only", [])
        fs["read_only"] = list({*existing, *readonly_paths})

    tmp = tempfile.NamedTemporaryFile(
        suffix=".yaml", delete=False, mode="w", prefix="nemo_oo_agents-policy-"
    )
    yaml.dump(policy, tmp)
    tmp.close()
    return Path(tmp.name)


def _create_env_provider(name: str, env_vars: tuple[str, ...]) -> None:
    """Create a temporary openshell credential provider from KEY=VALUE pairs."""
    subprocess.run(
        [
            "openshell",
            "provider",
            "create",
            "--name",
            name,
            "--type",
            "generic",
            *[a for kv in env_vars for a in ("--credential", kv)],
        ],
        check=True,
    )


def _delete_provider(name: str) -> None:
    subprocess.run(["openshell", "provider", "delete", name], check=False)


def _run_sandbox(
    bash_cmd: str,
    *,
    uploads: list[tuple[str, bool]],
    extra_domains: tuple[str, ...] = (),
    env_vars: tuple[str, ...] = (),
) -> None:
    """Spin up an ephemeral sandbox and run bash_cmd inside it."""
    _ensure_provider()
    name = f"{_PREFIX}{datetime.now().strftime('%Y%m%d%H%M%S')}"

    readonly_paths = [_sandbox_dest(p) for p, ro in uploads if ro]
    needs_patch = extra_domains or readonly_paths
    policy = _write_policy(extra_domains, readonly_paths) if needs_patch else _POLICY

    env_provider: str | None = None
    if env_vars:
        env_provider = f"{name}-env"
        _create_env_provider(env_provider, env_vars)

    providers = [_PROVIDER, *([env_provider] if env_provider else [])]
    args: list[str] = [
        "openshell",
        "sandbox",
        "create",
        "--name",
        name,
        "--policy",
        str(policy),
        *[a for p in providers for a in ("--provider", p)],
        *[a for p, _ in uploads for a in ("--upload", p)],
        "--",
        "bash",
        "-c",
        bash_cmd,
    ]
    result = subprocess.run(args, check=False)

    if needs_patch:
        policy.unlink(missing_ok=True)
    if env_provider:
        _delete_provider(env_provider)

    sys.exit(result.returncode)


# ------------------------------------------------------------------ #
# Command                                                             #
# ------------------------------------------------------------------ #


@click.command()
@click.argument("cmd", nargs=-1, required=True)
@click.option(
    "--upload",
    "upload_paths",
    multiple=True,
    metavar="PATH[:ro|:rw]",
    help=(
        "Path to upload into the sandbox (repeatable). "
        "Append :ro for read-only, :rw for read-write (default). "
        "Defaults to '.' when omitted. pyproject.toml always included."
    ),
)
@click.option(
    "--allow-domain",
    "extra_domains",
    multiple=True,
    metavar="HOST",
    help="Allow an additional domain (port 443) from inside the sandbox. Repeatable.",
)
@click.option(
    "--env",
    "env_vars",
    multiple=True,
    metavar="KEY=VALUE",
    help="Inject an environment variable into the sandbox. Repeatable.",
)
def command(
    cmd: tuple[str, ...],
    upload_paths: tuple[str, ...],
    extra_domains: tuple[str, ...],
    env_vars: tuple[str, ...],
) -> None:
    """Run a command in an isolated sandbox.

    CMD is the command to run. Use "tui" to launch the nemo_oo_agents TUI.

    Always requires a pyproject.toml in the current directory.
    Dependencies are installed via uv sync before the command runs.
    By default the entire current directory is uploaded; use --upload
    to specify one or more explicit paths instead.

    \b
    Examples:
        nemo_oo_agents sandbox -- nemo_oo_agents tui
        nemo_oo_agents sandbox -- python agent.py
        nemo_oo_agents sandbox --upload src --upload data -- python agent.py
        nemo_oo_agents sandbox --allow-domain api.myservice.com -- python agent.py
        nemo_oo_agents sandbox --env HF_TOKEN=abc123 -- python agent.py

    For advanced sandbox control use openshell directly.
    """
    _check_openshell()

    if not Path("pyproject.toml").exists():
        raise click.ClickException("No pyproject.toml found in the current directory.")

    # Parse upload paths; default to full cwd when none specified.
    uploads: list[tuple[str, bool]] = (
        [_parse_upload(p) for p in upload_paths] if upload_paths else [(".", False)]
    )
    local_paths = [p for p, _ in uploads]
    if "pyproject.toml" not in local_paths and "." not in local_paths:
        uploads.insert(0, ("pyproject.toml", False))

    bash_cmd = " && ".join(
        [
            "cd /sandbox",
            "uv sync",
            "export PATH=/sandbox/.venv/bin:$PATH",
            shlex.join(cmd),
        ]
    )
    _run_sandbox(bash_cmd, uploads=uploads, extra_domains=extra_domains, env_vars=env_vars)
