# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Serialization and storage errors."""

from nemo_oo_agents.errors import NemoOOAgentsError


class SerializationError(NemoOOAgentsError):
    """Error serializing agent state for a snapshot.

    Raised by StorageManager.save_snapshot() when a value can't be
    serialized (e.g., non-JSON-serializable attribute that isn't
    marked ``transient``).
    """

    pass


class DeserializationError(NemoOOAgentsError):
    """Error deserializing agent state from a snapshot.

    Raised when a serialized value cannot be restored — e.g., the
    envelope references a class not in the allowlist, the class
    cannot be imported, or the data doesn't match the expected format.
    """

    pass


class SnapshotNotFoundError(NemoOOAgentsError):
    """Snapshot ID not found in storage.

    Raised by Agent.load() when StorageManager.restore_snapshot()
    cannot find the given snapshot_id.
    """

    pass


class StorageNotConfiguredError(NemoOOAgentsError):
    """Agent.save() called without a StorageManager.

    Raised when attempting to save an agent that was constructed
    without a ``storage=`` parameter.
    """

    pass
