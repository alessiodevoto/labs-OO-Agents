# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Agent persistence — StorageManager protocol and implementations."""

from nemo_oo_agents.storage.in_memory import InMemoryStorageManager
from nemo_oo_agents.storage.json_snapshot import snapshot_from_json, snapshot_to_json
from nemo_oo_agents.storage.manager import StorageManager
from nemo_oo_agents.storage.markers import nosnapshot
from nemo_oo_agents.storage.snapshot import AgentSnapshot
from nemo_oo_agents.storage.sqlite import SQLiteStorageManager

__all__ = [
    "AgentSnapshot",
    "InMemoryStorageManager",
    "SQLiteStorageManager",
    "StorageManager",
    "nosnapshot",
    "snapshot_from_json",
    "snapshot_to_json",
]
