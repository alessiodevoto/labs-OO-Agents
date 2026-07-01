# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Long-term memory as a skill — a thin external adapter over the memory subsystem.

Registers as ``nemo.memory`` (so it lands on the agent as ``self.memory``). It reuses
the existing ``MemoryToolsMixin`` conscious tools verbatim and installs the existing
``MemoryManager`` in ``attach`` — no memory-internal architecture changes.
"""

from __future__ import annotations

from typing import Any

from nemo_oo_agents.memory.config import MemoryConfig
from nemo_oo_agents.memory.manager import MEMORY_SCHEMA_GUIDE, MemoryManager, MemoryToolsMixin
from nemo_oo_agents.memory.monitoring import MemoryStats
from nemo_oo_agents.memory.reflection import ReflectionReport
from nemo_oo_agents.skill import Skill

__all__ = ["MemorySkill"]


class MemorySkill(MemoryToolsMixin, Skill):
    """Long-term memory you own and curate.

    Write durable, reusable knowledge with
    ``self.memory.remember(content, type=..., importance=CRITICAL|HIGH|MEDIUM|LOW|TRIVIAL, tags=[...])``;
    retrieve with ``self.memory.recall(query)`` (associative) or ``self.memory.search(query)``
    (keyword); refine with ``self.memory.update_memory(id, ...)`` / ``self.memory.forget(id)``;
    link with ``self.memory.associate(a, b, relation)``; and consolidate with
    ``self.memory.reflect()``. ``self.memory.stats()`` returns usage counters.
    """

    __nosnapshot__ = True  # rebuilt + re-attached on resume

    def __init__(self, config: MemoryConfig | None = None) -> None:
        # Optional arg (discovery-safe: instantiated zero-arg). Defaults to enabled.
        self._mgr: MemoryManager | None = None
        self._config = config or MemoryConfig(enabled=True)

    def attach(self, agent: Any) -> None:
        super().attach(agent)  # sets self._agent
        config = self._config
        if config.enabled and config.instruct:
            agent.context_manager.set_static(config.instruct_block_key, MEMORY_SCHEMA_GUIDE)
            config = config.merge_with(instruct=False)
        self._mgr = MemoryManager.install(agent, config=config)

    def detach(self) -> None:
        if self._config.instruct and self._agent is not None:
            try:
                del self._agent.context_manager[self._config.instruct_block_key]
            except Exception:
                pass
        if self._mgr is not None:
            self._mgr.uninstall()
            self._mgr = None
        super().detach()

    # The single host hook the inherited MemoryToolsMixin bodies resolve through.
    def _memory_or_raise(self) -> MemoryManager:
        mgr = self._mgr
        if mgr is None or not mgr.config.enabled:
            raise RuntimeError("MemorySkill is not attached/enabled.")
        return mgr

    # remember / recall / search / update_memory / forget / associate: inherited verbatim.

    def reflect(self) -> ReflectionReport:
        """Consolidate memories: merge duplicates, form links, prune, reconsolidate."""
        return self._memory_or_raise().reflect()

    def stats(self) -> MemoryStats:
        """A snapshot of how this agent has used its memory (writes/recalls/…)."""
        return self._memory_or_raise().memory_stats()
