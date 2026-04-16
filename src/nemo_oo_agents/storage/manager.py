# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Storage manager protocol for agent persistence.

Defines the StorageManager interface — the single object users pass to
agents for full persistence support. It centralizes persistable events, including event streaming
(via an EventBackend) and state snapshots.

See docs/plans/2026-03-10-serialization.md for the full design.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent
    from nemo_oo_agents.runtime.event_manager import EventManager


@runtime_checkable
class StorageManager(Protocol):
    """Unified storage interface for agent persistence.

    Implementations centralize all agent storage:
    - Event management (owns the EventManager and its backend)
    - Snapshot save and restore

    The StorageManager is the single object users pass to agents
    for full persistence support.

    Both ``save_snapshot`` and ``restore_snapshot`` receive the agent
    directly. The implementation reads/writes the agent's internals
    however it sees fit (JSON, DB rows, protobuf, etc.).

    Example::

        storage = PostgresStorageManager("postgresql://...")
        agent = MyAgent(storage=storage)

        # Events stream automatically via storage.event_manager.
        # Snapshot explicitly:
        snapshot_id = agent.save()

        # Restore:
        agent = MyAgent.load(snapshot_id, storage=storage)
    """

    @property
    def event_manager(self) -> "EventManager":
        """The EventManager for this storage.

        Owns the EventBackend and provides the full event pipeline
        (add, query, collapse, subscribe). The Agent delegates all
        event operations through this.
        """
        ...

    def save_snapshot(self, agent: "Agent") -> str:
        """Save a snapshot of agent state.

        The implementation reads the agent's internals directly and
        serializes them. See class docstring for available internals.

        Args:
            agent: The agent to snapshot.

        Returns:
            A snapshot_id that can be used to load this snapshot later.
                The format is implementation-defined (UUID, file path,
                DB key, etc.).

        Raises:
            SerializationError: If a value can't be serialized by this
                implementation (e.g., non-JSON-serializable attribute
                that isn't marked ``transient``).
        """
        ...

    def restore_snapshot(self, snapshot_id: str, agent: "Agent") -> None:
        """Restore agent state from a previously saved snapshot.

        The implementation reads the snapshot from storage and applies
        it to the agent by writing to its internals (context blocks,
        method registry, attributes, event manager metadata).

        This is the symmetric counterpart to ``save_snapshot``:
        save reads the agent and writes to storage, restore reads
        storage and writes to the agent.

        Args:
            snapshot_id: The ID returned by ``save_snapshot()``.
            agent: The freshly constructed agent to restore into.

        Raises:
            SnapshotNotFoundError: If snapshot_id is not found in storage.
        """
        ...
