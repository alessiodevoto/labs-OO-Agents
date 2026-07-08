# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TUIAgent snapshot save and restore."""

from __future__ import annotations

from nemo_oo_agents_cli.tui.agent import TUIAgent
from nemo_oo_agents_cli.tui.config import AgentConfig

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents.unifiedllm import FakeLLMClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    db_path, config: AgentConfig | None = None, **kwargs
) -> tuple[TUIAgent, SQLiteStorageManager]:
    storage = SQLiteStorageManager(db_path)
    agent = TUIAgent(llm=FakeLLMClient(), config=config or AgentConfig(), storage=storage, **kwargs)
    return agent, storage


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


class TestSnapshotSave:
    def test_save_does_not_raise(self, tmp_path):
        """save_snapshot() must not raise — non-serializable fields are nosnapshot."""
        agent, storage = _make_agent(tmp_path / "s.db")
        try:
            storage.save_snapshot(agent)  # must not raise SerializationError
        finally:
            storage.close()


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


class TestSnapshotRestore:
    def test_restore_returns_true(self, tmp_path):
        """restore_latest_snapshot() returns True when a snapshot exists."""
        db = tmp_path / "s.db"

        agent, storage = _make_agent(db)
        storage.save_snapshot(agent)
        storage.close()

        agent2, storage2 = _make_agent(db)
        try:
            result = storage2.restore_latest_snapshot(agent2)
        finally:
            storage2.close()

        assert result is True

    def test_restore_returns_false_when_no_snapshot(self, tmp_path):
        """restore_latest_snapshot() returns False when no snapshot exists."""
        db = tmp_path / "s.db"
        agent, storage = _make_agent(db)
        try:
            result = storage.restore_latest_snapshot(agent)
        finally:
            storage.close()

        assert result is False

    def test_nosnapshot_fields_keep_fresh_values_after_restore(self, tmp_path):
        """Fields marked nosnapshot (shell, repo, libs, _config) are not overwritten."""
        db = tmp_path / "s.db"

        agent, storage = _make_agent(db)
        storage.save_snapshot(agent)
        storage.close()

        # Fresh agent with a distinguishable config
        config2 = AgentConfig(working_dir="/tmp/other")
        agent2, storage2 = _make_agent(db, config=config2)  # type: ignore[arg-type]
        original_shell = agent2.shell
        storage2.restore_latest_snapshot(agent2)
        storage2.close()

        # nosnapshot fields must not have been replaced by snapshot data
        assert agent2.shell is original_shell
        assert agent2._config is config2
