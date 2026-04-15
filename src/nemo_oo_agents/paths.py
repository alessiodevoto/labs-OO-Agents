# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Well-known filesystem paths for NeMo OO Agents.

Two root directories:

- **User dir** — namespaced under NAT's config directory at ``oo/``:
  ``~/.config/nat/oo/`` on Linux, ``~/Library/Application Support/nat/oo/``
  on macOS.  Respects the ``NAT_CONFIG_DIR`` environment variable.
  Overridable via ``NEMO_OO_USER_DIR``.

- **Project dir** (``<project-root>/.nemo_oo_agents/``) — local to the
  current project; config, optional trace files, library code.
  Overridable via ``NEMO_OO_PROJECT_DIR``.

Usage::

    from nemo_oo_agents.paths import get_user_dir, get_project_dir

    sessions = get_user_dir("sessions")        # ~/.config/nat/oo/sessions
    config   = get_project_dir("config.toml") # <root>/.nemo_oo_agents/config.toml
"""

import os
from pathlib import Path

#: Directory name used for the project-local directory.
DIR_NAME = ".nemo_oo_agents"


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

    Namespaced under NAT's config directory (``oo/`` subdirectory), so it
    co-locates with other NAT components.  Respects ``NAT_CONFIG_DIR`` if set.
    Override the entire base with ``NEMO_OO_USER_DIR``.

    Default locations:

    - Linux:  ``~/.config/nat/oo/``
    - macOS:  ``~/Library/Application Support/nat/oo/``

    Examples::

        get_user_dir()              # ~/.config/nat/oo/
        get_user_dir("sessions")    # ~/.config/nat/oo/sessions/
        get_user_dir("traces.db")   # ~/.config/nat/oo/traces.db
    """
    override = os.environ.get("NEMO_OO_USER_DIR")
    if override:
        base = Path(override)
    else:
        from platformdirs import user_config_dir

        nat_config = os.environ.get("NAT_CONFIG_DIR", user_config_dir(appname="nat"))
        base = Path(nat_config) / "oo"
    return base.joinpath(*parts) if parts else base


def get_project_dir(*parts: str) -> Path:
    """Return the project-local NeMo OO Agents directory, optionally joined with *parts*.

    Defaults to ``<project-root>/.nemo_oo_agents/`` where the project root is
    the nearest ancestor directory containing a ``pyproject.toml``.  Override
    with the ``NEMO_OO_PROJECT_DIR`` environment variable.

    Examples::

        get_project_dir()                  # <root>/.nemo_oo_agents/
        get_project_dir("config.toml")     # <root>/.nemo_oo_agents/config.toml
        get_project_dir("traces")          # <root>/.nemo_oo_agents/traces/
    """
    base_str = os.environ.get("NEMO_OO_PROJECT_DIR")
    base = Path(base_str) if base_str else find_project_root() / DIR_NAME
    return base.joinpath(*parts) if parts else base
