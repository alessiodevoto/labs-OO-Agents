# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NVIDIA-gateway bundled defaults for the NeMo OO Agents LLM registry.

Installing this package registers a ``nemo_oo_agents.bundled_configs``
entry-point that the core framework picks up automatically; the YAML
in :mod:`nemo_oo_agents_nvidia.data` becomes the lowest-priority layer
of the registry chain.

External users who don't want the NVIDIA aliases simply don't install
this package — there's no env-var toggle to remember.
"""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

_RESOURCE_PACKAGE = "nemo_oo_agents_nvidia.data"
_RESOURCE_NAME = "llm_config_default.yaml"


def get_default_config_path() -> Path | None:
    """Return the filesystem path of the bundled NVIDIA YAML, or ``None``.

    Returns ``None`` only when the resource cannot be materialised to a
    real on-disk file (zipped wheels, exotic install layouts). For
    standard installs this is the YAML's actual location inside the
    installed wheel.

    Registered as a ``nemo_oo_agents.bundled_configs`` entry-point;
    :func:`nemo_oo_agents.llm_config.llm_config_chain` calls it.
    """
    try:
        traversable = resources.files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME)
    except Exception:  # noqa: BLE001 — exotic Traversable backends raise TypeError/OSError
        logger.warning(
            "Could not resolve bundled LLM config resource %s:%s",
            _RESOURCE_PACKAGE,
            _RESOURCE_NAME,
            exc_info=True,
        )
        return None
    if not traversable.is_file():
        logger.debug(
            "Bundled LLM config resource is not a real file "
            "(zipped wheel or exotic install?); skipping. Resource: %s",
            traversable,
        )
        return None
    return Path(str(traversable))


__all__ = ["get_default_config_path"]
