# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for InMemoryStorageManager — specifically the save/restore error paths."""

import pytest

from nemo_oo_agents.errors.storage import StorageNotConfiguredError
from nemo_oo_agents.storage.in_memory import InMemoryStorageManager


class TestInMemoryStorageManager:
    def test_save_snapshot_raises_storage_not_configured_error(self):
        mgr = InMemoryStorageManager()
        with pytest.raises(StorageNotConfiguredError, match="does not support persistence"):
            mgr.save_snapshot(None)  # type: ignore[arg-type]

    def test_restore_snapshot_raises_storage_not_configured_error(self):
        mgr = InMemoryStorageManager()
        with pytest.raises(StorageNotConfiguredError, match="does not support persistence"):
            mgr.restore_snapshot("some-id", None)  # type: ignore[arg-type]
