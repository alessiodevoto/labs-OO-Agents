# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Memory-usage monitoring, debug logging, and counters.

Reuses the framework's existing observability instead of inventing a new one:

* **logging** — a dedicated ``nemo_oo_agents.memory`` logger; turn it up with
  ``logging.getLogger("nemo_oo_agents.memory").setLevel(logging.DEBUG)`` or the
  framework's ``enable_logging``.
* **event bus** — emits ``MemoryWritten`` / ``MemoryRecalled`` / ``MemoryInjected``
  / ``ReflectionCompleted`` events on the agent's ``EventManager`` with the
  ``RUNTIME_EVENT`` role, so they show up for any existing event/telemetry
  subscriber but never enter the LLM context.
* **counters** — a ``MemoryStats`` snapshot for programmatic monitoring (used by
  the benchmark to compare memory usage across runs).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import ClassVar

from nemo_oo_agents.context_blocks import EventBase
from nemo_oo_agents.context_blocks.roles import Role


# ---------------------------------------------------------------------------
# Runtime events (RUNTIME_EVENT role => never rendered into LLM context)
# ---------------------------------------------------------------------------
class MemoryWritten(EventBase):
    """A memory was encoded (or a duplicate reinforced)."""

    _role: ClassVar[Role] = Role.RUNTIME_EVENT
    memory_id: str = ""
    mem_type: str = "info"
    op: str = "add"  # "add" | "reinforce"
    importance: float = 5.0


class MemoryRecalled(EventBase):
    """Memories were recalled for a query."""

    _role: ClassVar[Role] = Role.RUNTIME_EVENT
    query: str = ""
    n_results: int = 0
    hops: int = 0


class MemoryInjected(EventBase):
    """Spontaneous-association block was (re)injected into context."""

    _role: ClassVar[Role] = Role.RUNTIME_EVENT
    n_memories: int = 0
    chars: int = 0


class ReflectionCompleted(EventBase):
    """An offline consolidation pass finished."""

    _role: ClassVar[Role] = Role.RUNTIME_EVENT
    merged: int = 0
    edges_added: int = 0
    rescored: int = 0
    pruned: int = 0
    created: int = 0


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
@dataclass
class MemoryStats:
    """Running counters of how the agent has used its memory this process."""

    writes: int = 0  # new memories encoded
    reinforced: int = 0  # dedup-on-write hits (existing memory strengthened)
    recalls: int = 0  # recall() / search() calls
    recalled_items: int = 0  # total memories returned by recalls
    injections: int = 0  # spontaneous context-block (re)injections
    injected_chars: int = 0  # cumulative chars injected
    reflections: int = 0  # consolidation passes
    merged: int = 0  # duplicates merged during reflection
    edges_added: int = 0  # graph edges formed during reflection
    pruned: int = 0  # memories forgotten (archived/deleted)
    store_size: int = 0  # active memories in the store (set on snapshot)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"writes={self.writes} reinforced={self.reinforced} recalls={self.recalls} "
            f"recalled_items={self.recalled_items} injections={self.injections} "
            f"injected_chars={self.injected_chars} reflections={self.reflections} merged={self.merged} "
            f"edges_added={self.edges_added} pruned={self.pruned} store_size={self.store_size}"
        )
