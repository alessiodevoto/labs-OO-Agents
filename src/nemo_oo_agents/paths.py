# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Well-known filesystem paths for NeMo OO Agents.

Two root directories, one name:

- **User dir** (``~/.nemo_oo_agents/``) — global to the user; sessions,
  viewer database, anything that should persist across projects.
- **Project dir** (``<project-root>/.nemo_oo_agents/``) — local to the
  current project; config, optional trace files, library code.

Both are overridable via environment variables::

    NEMO_OO_USER_DIR=/data/nemo_agents nemo_oo_agents
    NEMO_OO_PROJECT_DIR=/shared/.nemo_oo_agents nemo_oo_agents

Usage::

    from nemo_oo_agents.paths import get_user_dir, get_project_dir

    sessions = get_user_dir("sessions")        # ~/.nemo_oo_agents/sessions
    config   = get_project_dir("config.toml") # <root>/.nemo_oo_agents/config.toml
"""

import os
from pathlib import Path

#: Directory name used for both user-global and project-local directories.
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

    Defaults to ``~/.nemo_oo_agents/``.  Override with the ``NEMO_OO_USER_DIR``
    environment variable.

    Examples::

        get_user_dir()              # ~/.nemo_oo_agents/
        get_user_dir("sessions")    # ~/.nemo_oo_agents/sessions/
        get_user_dir("traces.db")   # ~/.nemo_oo_agents/traces.db
    """
    base_str = os.environ.get("NEMO_OO_USER_DIR")
    base = Path(base_str) if base_str else Path.home() / DIR_NAME
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
