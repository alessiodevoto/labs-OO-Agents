"""Agent persistence — StorageManager protocol and implementations."""

from agent006.storage.in_memory import InMemoryStorageManager
from agent006.storage.json_snapshot import snapshot_from_json, snapshot_to_json
from agent006.storage.manager import StorageManager
from agent006.storage.markers import nosnapshot
from agent006.storage.snapshot import AgentSnapshot
from agent006.storage.sqlite import SQLiteStorageManager

__all__ = [
    "AgentSnapshot",
    "InMemoryStorageManager",
    "SQLiteStorageManager",
    "StorageManager",
    "nosnapshot",
    "snapshot_from_json",
    "snapshot_to_json",
]
