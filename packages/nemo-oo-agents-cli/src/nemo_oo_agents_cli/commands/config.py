# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``nemo oo config`` — inspect and customize the LLM-registry config chain.

Subcommands:

- ``show``   — print the resolved file chain (which layers are loaded).
- ``path``   — print the user-level YAML path (where ``eject`` writes).
- ``eject``  — copy a bundled-defaults YAML to the user-level path so
               you can edit it locally.

The config chain (last wins):

1. Bundled defaults — every package that registers under the
   ``nemo_oo_agents.bundled_configs`` entry-point group. Install
   ``nemo-oo-agents-nvidia`` for the NVIDIA-gateway aliases.
2. User config at ``~/.config/nat/oo/llm_config.yaml``
   (overridable via ``NEMO_OO_USER_DIR``).
3. Project-local ``.nemo_oo_agents/llm_config.yaml``.
4. ``NEMO_OO_LLM_CONFIG`` env var — comma-separated YAML paths;
   the global override, highest priority.
"""

import os
import shutil
from pathlib import Path

import click


def _user_config_path() -> Path:
    """Where ``eject`` writes and the registry's user layer reads from."""
    from nemo_oo_agents.paths import get_user_dir

    return get_user_dir("llm_config.yaml")


def _project_config_path() -> Path:
    """Where the registry's project-local layer reads from."""
    from nemo_oo_agents.paths import get_project_dir

    return get_project_dir("llm_config.yaml")


def _count_aliases(path: Path) -> int | None:
    """Return the count of ``models:`` entries in a YAML file, or None on error."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return 0
    models = data.get("models")
    if not isinstance(models, dict):
        return 0
    # Count non-None entries (null entries are removal directives, not aliases).
    return sum(1 for v in models.values() if v is not None)


@click.group()
def command():
    """Inspect and customize the LLM-registry config chain."""


@command.command("show")
def show_cmd():
    """Print the resolved config-file chain.

    Useful for debugging "why is my model alias not found" — shows each
    layer, whether it's present, and how many aliases it contributes.

    For each candidate layer, output includes a "(deduplicated …)" note
    if the file's resolved path is *also* reachable through a
    higher-priority layer — :func:`llm_config_chain` keeps only the
    higher slot, so the lower one isn't actually loaded.
    """
    from nemo_oo_agents.llm_config import (
        bundled_config_paths,
        llm_config_chain,
    )

    # Resolve the chain once up-front so each candidate line below can
    # accurately note whether it survived deduplication.
    resolved_chain = llm_config_chain()
    resolved_set = {p.resolve() for p in resolved_chain}

    def _dedup_suffix(candidate_path: Path) -> str:
        """Return ``""`` if loaded, else a "(deduplicated …)" annotation."""
        try:
            return (
                ""
                if candidate_path.resolve() in resolved_set
                else "  (deduplicated — same resolved path as a higher-priority layer)"
            )
        except OSError:
            return ""

    # Layer 1 — bundled defaults (entry-point providers)
    click.echo("Bundled defaults:")
    bundled = bundled_config_paths()
    if not bundled:
        click.echo("  (none registered — install nemo-oo-agents-nvidia or another provider)")
    else:
        for b in bundled:
            n = _count_aliases(b)
            alias_suffix = f"({n} aliases)" if n is not None else "(unreadable)"
            click.echo(f"  {b}  {alias_suffix}{_dedup_suffix(b)}")

    # Layer 2 — user
    user_path = _user_config_path()
    click.echo(f"User config ({user_path}):")
    if user_path.exists():
        n = _count_aliases(user_path)
        suffix = f"  ({n} aliases)" if n is not None else "  (unreadable)"
        click.echo(f"  present{suffix}{_dedup_suffix(user_path)}")
    else:
        click.echo("  not present")

    # Layer 3 — project-local
    project_path = _project_config_path()
    click.echo(f"Project config ({project_path}):")
    if project_path.exists():
        n = _count_aliases(project_path)
        suffix = f"  ({n} aliases)" if n is not None else "  (unreadable)"
        click.echo(f"  present{suffix}{_dedup_suffix(project_path)}")
    else:
        click.echo("  not present")

    # Layer 4 — env var (global override, highest priority)
    env_val = os.environ.get("NEMO_OO_LLM_CONFIG", "").strip()
    click.echo("NEMO_OO_LLM_CONFIG (global override):")
    if not env_val:
        click.echo("  not set")
    else:
        for raw in env_val.split(","):
            raw = raw.strip()
            if not raw:
                continue
            p = Path(raw).expanduser().resolve()
            if p.exists():
                n = _count_aliases(p)
                suffix = f"  ({n} aliases)" if n is not None else "  (unreadable)"
                click.echo(f"  {p}{suffix}{_dedup_suffix(p)}")
            else:
                click.echo(f"  {p}  (does not exist)")

    # Total — populate MODELS via the same resolved chain so the count
    # is consistent with the dedup-annotated layers above.
    from nemo_oo_agents.unifiedllm import MODELS, reload_registry

    reload_registry(*resolved_chain)
    click.echo("")
    click.echo(f"Total: {len(MODELS)} model aliases registered")

    # Surface which API key env vars the loaded aliases reference, and
    # whether each is set. This is the "why isn't my key working"
    # diagnostic.
    referenced = {
        cfg["api_key_env"]
        for cfg in MODELS.values()
        if isinstance(cfg, dict) and cfg.get("api_key_env")
    }
    if referenced:
        click.echo("")
        click.echo("API key env vars referenced:")
        for var in sorted(referenced):
            present = "set" if os.environ.get(var) else "NOT SET"
            click.echo(f"  {var:30s}  ({present})")


@command.command("path")
def path_cmd():
    """Print the user-level config path (where ``eject`` writes)."""
    click.echo(str(_user_config_path()))


@command.command("eject")
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing user-level config file.",
)
def eject_cmd(force: bool):
    """Copy a bundled-defaults YAML to the user-level config path.

    The user-level path is ``~/.config/nat/oo/llm_config.yaml`` (XDG;
    override with ``NEMO_OO_USER_DIR``). Bundled defaults still load
    as a lower-priority layer, so the ejected copy *adds* to /
    overrides on top of them — you can prune the ejected file to just
    the aliases you want to customize.

    When multiple bundled-config providers are registered, ``eject``
    refuses (rather than silently picking one). Install only the one
    you want to use as the editing baseline, or write a hand-built
    ``llm_config.yaml`` directly.
    """
    from nemo_oo_agents.llm_config import bundled_config_paths

    target = _user_config_path()
    if target.exists() and not force:
        click.echo(
            f"Refusing to overwrite existing file: {target}\nRe-run with --force to overwrite.",
            err=True,
        )
        raise SystemExit(1)

    bundled = bundled_config_paths()
    if not bundled:
        click.echo(
            "No bundled-defaults provider is installed. Nothing to eject.\n"
            "Install `nemo-oo-agents-nvidia` (or another provider) first.",
            err=True,
        )
        raise SystemExit(1)
    if len(bundled) > 1:
        formatted = "\n  ".join(str(b) for b in bundled)
        click.echo(
            "Multiple bundled-defaults providers are registered; refusing to "
            "pick one to eject. Providers:\n  " + formatted + "\n"
            "Uninstall all but the one you want as the eject baseline.",
            err=True,
        )
        raise SystemExit(1)
    source = bundled[0]

    # Refuse to clobber a symlink target — managed-dotfiles users
    # routinely symlink ~/.config files into a versioned dir;
    # ``shutil.copyfile`` would silently write through the symlink to
    # the upstream target.
    if target.is_symlink():
        click.echo(
            f"Target is a symlink ({target} → {os.readlink(target)}); refusing to overwrite.\n"
            "Remove the symlink first, or write to a different path via NEMO_OO_USER_DIR.",
            err=True,
        )
        raise SystemExit(1)

    # Refuse to clobber a directory at the target path.
    if target.is_dir():
        click.echo(
            f"Target path is a directory: {target}\nRemove or rename it first.",
            err=True,
        )
        raise SystemExit(1)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        click.echo(f"Cannot create parent dir {target.parent}: {exc}", err=True)
        raise SystemExit(1) from exc

    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        click.echo(f"Cannot write to {target}: {exc}", err=True)
        raise SystemExit(1) from exc

    click.echo(f"Wrote {target}")
    click.echo(
        "Edit it to customize. `nemo oo` will load it as a layer on top of the bundled defaults."
    )

    if os.environ.get("NEMO_OO_LLM_CONFIG", "").strip():
        click.echo("")
        click.echo(
            "Note: $NEMO_OO_LLM_CONFIG is set; paths in that variable load *after* the file you "
            "just wrote and may override it. Run `nemo oo config show` to inspect resolution."
        )
