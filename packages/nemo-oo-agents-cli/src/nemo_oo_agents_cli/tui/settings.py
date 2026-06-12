# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Layered YAML settings for the NeMo OO Agents TUI.

This is the TUI half of the project's "one config story": TUI settings
live in ``settings.yaml`` next to ``llm_config.yaml`` and ``secrets.yaml``,
share the same directories, and are discovered through the same
:func:`nemo_oo_agents.layered_config.load_layered_yaml` helper.

The file is a direct serialisation of the :class:`Config` model tree
(``tui:`` / ``agent:`` sections, Pydantic field names), so it
round-trips: :func:`dump_settings` writes a config and :func:`load_settings`
reads it back identically.

Precedence (low → high, last wins) is the shared layered chain:

1. Model defaults (in code).
2. ``~/.config/nemo_oo/settings.yaml`` (user).
3. ``<project-root>/.nemo_oo/settings.yaml`` (project).
4. ``NEMO_OO_SETTINGS`` env var — comma-separated YAML paths.

CLI flags are layered on top of this by :meth:`Config.load`.

.. note::
   This module lives in the CLI package rather than core because it
   binds to :class:`Config`/:class:`TUIConfig`, which are defined here;
   core cannot import them without a circular dependency. The *generic*
   layered-loading machinery is in
   :mod:`nemo_oo_agents.layered_config`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.yaml"
SETTINGS_ENV_VAR = "NEMO_OO_SETTINGS"

# Per-field coercion when reading YAML scalars into config fields. We set
# fields directly (no assignment-time validation), so Path-typed fields are
# coerced here. Dotted path (section.field) → callable; unlisted = set as-is.
_COERCE: dict[str, Any] = {
    "tui.mcp_file": lambda v: Path(v),
    "tui.libs_dirs": lambda v: [Path(p) for p in (v if isinstance(v, list) else [v])],
    "tui.trace_dir": lambda v: Path(v) if v is not None else None,
}

# Fields that are computed / runtime-only and must NOT be persisted or
# applied from file (skills_dirs is derived from discovery + CLI in
# Config.load; the no_* flags are per-invocation).
_SKIP_FIELDS = {"tui.skills_dirs", "no_splash", "no_trace"}


def load_settings(cfg: Config) -> Config:
    """Apply layered ``settings.yaml`` onto *cfg* in place and return it.

    Reads the merged settings dict (user → project → env, last wins,
    ``null`` deletes) and sets matching config fields. Unknown keys
    are warned about and skipped so a stale file never crashes startup.
    """
    from nemo_oo_agents.layered_config import load_layered_yaml

    data = load_layered_yaml(SETTINGS_FILENAME, SETTINGS_ENV_VAR)
    for section in ("tui", "agent"):
        sect = data.get(section)
        if isinstance(sect, dict):
            _apply_section(getattr(cfg, section), sect, section)
    return cfg


def _apply_section(obj: Any, data: dict[str, Any], prefix: str) -> None:
    """Recursively set config-model fields on *obj* from *data*."""
    for key, value in data.items():
        dotted = f"{prefix}.{key}"
        if dotted in _SKIP_FIELDS:
            continue
        if not hasattr(obj, key):
            logger.warning("Unknown settings key %r — ignoring", dotted)
            continue
        current = getattr(obj, key)
        if isinstance(value, dict) and isinstance(current, BaseModel):
            _apply_section(current, value, dotted)
            continue
        coerce = _COERCE.get(dotted)
        setattr(obj, key, coerce(value) if coerce else value)


def settings_to_dict(cfg: Config) -> dict[str, Any]:
    """Serialise the persistable fields of *cfg* to a YAML-friendly dict.

    Inverse of :func:`load_settings`: ``load_settings(Config()) ==``
    a config built by applying ``settings_to_dict(Config())`` back.
    Paths become strings; ``skills_dirs`` and runtime flags are omitted.
    """
    return {
        "tui": _model_to_dict(cfg.tui, "tui"),
        "agent": _model_to_dict(cfg.agent, "agent"),
    }


def _model_to_dict(obj: BaseModel, prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in type(obj).model_fields:
        dotted = f"{prefix}.{name}"
        if dotted in _SKIP_FIELDS:
            continue
        value = getattr(obj, name)
        if isinstance(value, BaseModel):
            out[name] = _model_to_dict(value, dotted)
        elif isinstance(value, Path):
            out[name] = str(value)
        elif isinstance(value, list):
            out[name] = [str(v) if isinstance(v, Path) else v for v in value]
        else:
            out[name] = value
    return out


def dump_settings(cfg: Config) -> str:
    """Return the YAML text for *cfg* (round-trips with :func:`load_settings`)."""
    import yaml

    return yaml.safe_dump(settings_to_dict(cfg), sort_keys=False)


def settings_present() -> bool:
    """True if a ``settings.yaml`` exists in any layer (user/project/env)."""
    from nemo_oo_agents.layered_config import layered_paths

    return bool(layered_paths(SETTINGS_FILENAME, SETTINGS_ENV_VAR))


# Commented scaffold written on first run. Everything is commented out so
# the file documents the schema without overriding any defaults.
SETTINGS_TEMPLATE = """\
# NeMo OO Agents — TUI settings
#
# Layered, last wins:
#   1. built-in defaults
#   2. this file (user:    ~/.config/nemo_oo/settings.yaml)
#   3. project file:        .nemo_oo/settings.yaml
#   4. $NEMO_OO_SETTINGS    (comma-separated YAML paths)
#
# All keys are optional; uncomment only what you want to change.
# `null` removes a key inherited from a lower layer.

tui:
  # LLM model alias (from the unifiedllm registry) or a litellm model name.
  # default_model: {default_model}

  # Show the agent's Python execution panels.
  # show_python: false

  # Vi keybindings in the input prompt.
  # vi_mode: false

  # Write trace files here (relative to project root, or ":project:").
  # trace_dir: .nemo_oo/traces

  # External library-skill directories (each subdir with a pyproject.toml).
  # libs_dirs: []

  # MCP servers, declared inline (preferred over a separate .mcp.json).
  # Use env vars for secrets; ${VAR} is expanded when the server connects.
  # mcp_auto_connect: [maas]
  # mcp_servers:
  #   maas:
  #     url: https://maas.stg.astra.nvidia.com/maas/confluence/mcp
  #     transport: streamable-http
  #     headers:
  #       Authorization: "Bearer ${MAAS_API_KEY}"

# agent:
#   orchestrator: false
#   working_dir: "."
#   summarization:
#     policy: token_budget   # token_budget | sliding_window | none
#     max_tokens: null       # null = 80% of the model's context window
"""


def render_settings_template(cfg: Config) -> str:
    """Render the commented first-run scaffold for *cfg*.

    Uses ``str.replace`` rather than ``str.format`` because the template
    contains literal braces (e.g. ``${MAAS_API_KEY}`` in the MCP example)
    that ``format()`` would treat as fields and raise ``KeyError`` on.
    """
    return SETTINGS_TEMPLATE.replace("{default_model}", cfg.tui.default_model)
