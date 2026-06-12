# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Well-known filesystem paths for NeMo OO Agents.

Two root directories:

- **User dir** — ``~/.config/nemo_oo/`` on every platform (honors
  ``XDG_CONFIG_HOME`` if set). A single, predictable location so the
  installer and the runtime always agree. Override the whole base with
  ``NEMO_OO_USER_DIR``.

- **Project dir** (``<project-root>/.nemo_oo/``) — local to the current
  project; config, optional trace files, library code. Override with
  ``NEMO_OO_PROJECT_DIR``.

Usage::

    from nemo_oo_agents.paths import get_user_dir, get_project_dir

    sessions = get_user_dir("sessions")          # ~/.config/nemo_oo/sessions
    config   = get_project_dir("settings.yaml")  # <root>/.nemo_oo/settings.yaml
"""

import os
from pathlib import Path

#: Directory name used for the project-local directory.
DIR_NAME = ".nemo_oo"


def find_project_root() -> Path:
    """Walk up from this file to find the project root (where pyproject.toml lives).

    Falls back to ``Path.cwd()`` if no ``pyproject.toml`` is found (e.g. when
    the package is installed into site-packages rather than run from source).
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def get_user_dir(*parts: str) -> Path:
    """Return the user-global NeMo OO Agents directory, optionally joined with *parts*.

    Location is ``~/.config/nemo_oo/`` on every platform (honoring
    ``XDG_CONFIG_HOME`` when set), so the path is predictable and the
    installer (``install.sh``) and the runtime always resolve the same
    place. Override the entire base with ``NEMO_OO_USER_DIR``.

    Examples::

        get_user_dir()              # ~/.config/nemo_oo/
        get_user_dir("sessions")    # ~/.config/nemo_oo/sessions/
        get_user_dir("traces.db")   # ~/.config/nemo_oo/traces.db
    """
    override = os.environ.get("NEMO_OO_USER_DIR")
    if override:
        base = Path(override)
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(xdg) if xdg else Path.home() / ".config"
        base = config_home / "nemo_oo"
    return base.joinpath(*parts) if parts else base


def get_project_dir(*parts: str) -> Path:
    """Return the project-local NeMo OO Agents directory, optionally joined with *parts*.

    Defaults to ``<project-root>/.nemo_oo/`` where the project root is
    the nearest ancestor directory containing a ``pyproject.toml``.  Override
    with the ``NEMO_OO_PROJECT_DIR`` environment variable.

    Examples::

        get_project_dir()                   # <root>/.nemo_oo/
        get_project_dir("settings.yaml")    # <root>/.nemo_oo/settings.yaml
        get_project_dir("traces")           # <root>/.nemo_oo/traces/
    """
    base_str = os.environ.get("NEMO_OO_PROJECT_DIR")
    base = Path(base_str) if base_str else find_project_root() / DIR_NAME
    return base.joinpath(*parts) if parts else base
