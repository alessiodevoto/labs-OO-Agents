# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LLM-registry config-file discovery.

The ``unifiedllm`` package is intentionally ignorant of project-wide
filesystem conventions (``paths.py``). This module is where the
project's path infrastructure combines with the LLM-config discovery
rules into a single helper:

- :func:`llm_config_chain` returns a priority-ordered list of YAML
  files (lowest priority first). Pass it to
  :func:`nemo_oo_agents.unifiedllm.reload_registry` to populate the
  registry.
- :func:`bundled_config_paths` returns the on-disk paths of all
  bundled-default YAMLs registered via the
  ``nemo_oo_agents.bundled_configs`` entry-point group. External
  packages (e.g. ``nemo-oo-agents-nvidia``) register their YAML there
  and the core picks them up automatically; install / don't install
  to opt in / out.

Priority (last wins):

1. Bundled defaults — every entry-point in
   ``nemo_oo_agents.bundled_configs`` (lowest)
2. ``get_user_dir("llm_config.yaml")`` — user-global default
3. ``get_project_dir("llm_config.yaml")`` — project-local config
4. ``NEMO_OO_LLM_CONFIG`` env var (comma-separated paths) — global
   override; highest priority so a shell session can override
   project / user files without editing them.
"""

from __future__ import annotations

import logging
import os
from importlib import metadata
from pathlib import Path

from nemo_oo_agents.paths import get_project_dir, get_user_dir

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "llm_config.yaml"
_CONFIG_ENV_VAR = "NEMO_OO_LLM_CONFIG"
_BUNDLED_ENTRY_POINT_GROUP = "nemo_oo_agents.bundled_configs"


def bundled_config_paths() -> list[Path]:
    """Return on-disk paths for every registered bundled-default YAML.

    Iterates entry-points in the ``nemo_oo_agents.bundled_configs``
    group, calls each (they're zero-arg callables returning
    ``Path | None``), and returns the resulting list with ``None``\\ s
    filtered out. Entries are sorted by entry-point name for stable
    ordering.

    External packages register their bundled defaults like this::

        # pyproject.toml
        [project.entry-points."nemo_oo_agents.bundled_configs"]
        nvidia = "nemo_oo_agents_nvidia:get_default_config_path"

    The callable receives no arguments and returns the absolute path
    of its YAML, or ``None`` if the resource can't be materialised
    (zipped wheels, exotic install layouts).
    """
    out: list[Path] = []
    try:
        eps = metadata.entry_points(group=_BUNDLED_ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — importlib.metadata can raise on broken installs
        logger.warning("Failed to enumerate bundled-config entry points", exc_info=True)
        return out

    for ep in sorted(eps, key=lambda e: e.name):
        try:
            func = ep.load()
            path = func()
        except Exception:  # noqa: BLE001 — third-party entry-point code, be defensive
            logger.warning(
                "Bundled-config entry-point %r failed to load",
                ep.name,
                exc_info=True,
            )
            continue
        if path is None:
            continue
        out.append(Path(path))
    return out


def _resolved_if_exists(path: Path) -> Path | None:
    """Return ``path.resolve()`` if the file exists, else ``None``."""
    try:
        if path.exists():
            return path.resolve()
    except OSError:
        # Permission or stat failure — treat as missing.
        return None
    return None


def _env_paths() -> list[tuple[Path, Path | None]]:
    """Parse the ``NEMO_OO_LLM_CONFIG`` env var into ``(raw, resolved)`` pairs.

    Resolved is ``None`` when the file doesn't exist (a warning is
    emitted by :func:`llm_config_chain`).
    """
    raw = os.environ.get(_CONFIG_ENV_VAR, "").strip()
    if not raw:
        return []
    out: list[tuple[Path, Path | None]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        p = Path(entry).expanduser()
        out.append((p, _resolved_if_exists(p)))
    return out


def llm_config_chain() -> list[Path]:
    """Return YAML config files for the LLM registry, lowest priority first.

    Layers, in order:

    1. Bundled defaults — every package that registers under the
       ``nemo_oo_agents.bundled_configs`` entry-point group (in
       entry-point-name order). Install ``nemo-oo-agents-nvidia`` for
       the NVIDIA-gateway aliases; install nothing for an OSS-only
       registry.
    2. ``get_user_dir("llm_config.yaml")`` — user-global default
       (``~/.config/nat/oo/llm_config.yaml`` on Linux).
    3. ``get_project_dir("llm_config.yaml")`` — project-local config
       (``<project-root>/.nemo_oo_agents/llm_config.yaml``).
    4. ``NEMO_OO_LLM_CONFIG`` env var — comma-separated YAML paths.
       Highest priority: an explicit env var always wins so users can
       override project / user config from a shell session without
       editing files.

    Only files that actually exist are returned. Each path is
    canonicalised via :meth:`pathlib.Path.resolve` so duplicates and
    symlink aliases collapse: if the same resolved path appears in
    multiple layers, the **lower-priority occurrence is dropped and
    the higher-priority position is kept** — so the last-wins merge
    in :func:`nemo_oo_agents.unifiedllm.reload_registry` lines up
    with the layering described here.

    Paths from ``NEMO_OO_LLM_CONFIG`` that don't exist log a warning.
    User and project layer paths are silently skipped when missing.
    """
    bundled = [
        resolved for b in bundled_config_paths() if (resolved := _resolved_if_exists(b)) is not None
    ]
    user = _resolved_if_exists(get_user_dir(_CONFIG_FILENAME))
    project = _resolved_if_exists(get_project_dir(_CONFIG_FILENAME))
    env_entries = _env_paths()

    # Walk layers in priority order and keep the *latest* (highest
    # priority) occurrence of any resolved path.
    chain: list[Path] = []
    seen: dict[Path, int] = {}

    def _push(resolved: Path) -> None:
        if resolved in seen:
            chain.pop(seen[resolved])
            for k, v in seen.items():
                if v > seen[resolved]:
                    seen[k] = v - 1
            seen.pop(resolved)
        seen[resolved] = len(chain)
        chain.append(resolved)

    for path in bundled:
        _push(path)

    if user is not None:
        _push(user)

    if project is not None:
        _push(project)

    for raw, resolved in env_entries:
        if resolved is None:
            logger.warning("%s path does not exist: %s", _CONFIG_ENV_VAR, raw)
            continue
        _push(resolved)

    return chain


__all__ = ["bundled_config_paths", "llm_config_chain"]
