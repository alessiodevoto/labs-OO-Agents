"""Tests for TUIAgent snapshot save and restore."""

from __future__ import annotations

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents_cli.tui.agent import TUIAgent
from nemo_oo_agents_cli.tui.config import AgentConfig
from unifiedllm import FakeLLMClient

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

    def test_save_with_non_default_phase(self, tmp_path):
        """save_snapshot() works when _phase and _workflow_state are non-default."""
        agent, storage = _make_agent(tmp_path / "s.db")
        agent._phase = "implementing"
        agent._workflow_state = {"plan": "do stuff", "step": 3}
        try:
            storage.save_snapshot(agent)
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

    def test_phase_and_workflow_state_round_trip(self, tmp_path):
        """_phase and _workflow_state survive a save/restore cycle."""
        db = tmp_path / "s.db"

        agent, storage = _make_agent(db)
        agent._phase = "verifying"
        agent._workflow_state = {"plan": "my plan", "done": ["step1", "step2"]}
        storage.save_snapshot(agent)
        storage.close()

        agent2, storage2 = _make_agent(db)
        storage2.restore_latest_snapshot(agent2)
        storage2.close()

        assert agent2._phase == "verifying"
        assert agent2._workflow_state == {"plan": "my plan", "done": ["step1", "step2"]}

    def test_nosnapshot_fields_keep_fresh_values_after_restore(self, tmp_path):
        """Fields marked nosnapshot (bash, files, libs, _config) are not overwritten."""
        db = tmp_path / "s.db"

        agent, storage = _make_agent(db)
        storage.save_snapshot(agent)
        storage.close()

        # Fresh agent with a distinguishable config
        config2 = AgentConfig(working_dir="/tmp/other")
        agent2, storage2 = _make_agent(db, config=config2)  # type: ignore[arg-type]
        original_bash = agent2.bash
        storage2.restore_latest_snapshot(agent2)
        storage2.close()

        # nosnapshot fields must not have been replaced by snapshot data
        assert agent2.bash is original_bash
        assert agent2._config is config2
