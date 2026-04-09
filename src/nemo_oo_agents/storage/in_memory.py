# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-memory StorageManager — no persistence, drop-in for current behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nemo_oo_agents.runtime.event_backend import InMemoryBackend
from nemo_oo_agents.runtime.event_manager import EventManager

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent


class InMemoryStorageManager:
    """StorageManager that keeps everything in memory.

    This is the default when no storage is configured. Events live in
    an ``InMemoryBackend`` (same as today), and save/restore are no-ops
    that raise — there's nowhere to persist to.

    This allows us to centralize storage in StorageManager, rather than
    having to special-case "no storage provided".
    """

    def __init__(self) -> None:
        self._event_manager = EventManager(backend=InMemoryBackend())

    @property
    def event_manager(self) -> EventManager:
        return self._event_manager

    def save_snapshot(self, agent: Agent) -> str:
        from nemo_oo_agents.errors.storage import StorageNotConfiguredError

        raise StorageNotConfiguredError(
            "InMemoryStorageManager does not support persistence. "
            "Pass a persistent StorageManager to enable save/restore."
        )

    def restore_snapshot(self, snapshot_id: str, agent: Agent) -> None:
        from nemo_oo_agents.errors.storage import StorageNotConfiguredError

        raise StorageNotConfiguredError(
            "InMemoryStorageManager does not support persistence. "
            "Pass a persistent StorageManager to enable save/restore."
        )
