# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from nemo_oo_agents_cli.tui.config import Config


def _configure_project(monkeypatch, tmp_path):
    project_dir = tmp_path / ".nemo_oo_agents"
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(project_dir))
    import nemo_oo_agents_cli.tui.session_manager as session_manager

    monkeypatch.setattr(session_manager, "SESSIONS_DIR", project_dir / "sessions")
    return project_dir


def test_tui_memory_config_defaults_to_off():
    cfg = Config.load()
    assert cfg.tui.memory == "off"
    assert cfg.tui.memory_agents == {}
    assert cfg.tui.memory_path is None


def test_tui_memory_config_overrides_path(tmp_path):
    db = tmp_path / "custom-memory.db"
    cfg = Config.load(memory="session", memory_path=str(db))
    assert cfg.tui.memory == "session"
    assert cfg.tui.memory_path == db


def test_tui_memory_config_loads_per_agent_preferences():
    cfg = Config.load(memory_agents={"my.module:Agent": "session"})
    assert cfg.tui.memory == "off"
    assert cfg.tui.memory_agents == {"my.module:Agent": "session"}


@pytest.mark.asyncio
async def test_bootstrap_default_memory_off_does_not_activate_memory(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.bootstrap import bootstrap

    _configure_project(monkeypatch, tmp_path)
    cfg = Config()
    cfg.tui.default_model = "test-model"
    cfg.agent.summarization.policy = "none"
    result = await bootstrap(cfg)
    try:
        assert not hasattr(result.agent, "memory")
        assert "nemo.memory" not in result.agent.skills.activated()
    finally:
        if result.session_manager is not None:
            result.session_manager.close()


@pytest.mark.asyncio
async def test_bootstrap_per_agent_memory_is_session_scoped(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.bootstrap import bootstrap

    from nemo_oo_agents.memory.memory_skill import MemorySkill

    project_dir = _configure_project(monkeypatch, tmp_path)
    cfg = Config()
    cfg.tui.default_model = "test-model"
    cfg.tui.memory_agents["nemo_oo_agents_cli.tui.agent:TUIAgent"] = "session"
    cfg.agent.summarization.policy = "none"
    result = await bootstrap(cfg)
    try:
        assert isinstance(result.agent.memory, MemorySkill)
        assert result.agent.memory._mgr is not None
        assert result.session_id is not None
        assert result.agent.memory._mgr.store.path == str(
            project_dir / "sessions" / f"{result.session_id}-memory.db"
        )
        assert "nemo.memory" in result.agent.skills.activated()
    finally:
        if result.session_manager is not None:
            result.session_manager.close()


@pytest.mark.asyncio
async def test_configure_tui_memory_repoints_session_scope(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.bootstrap import bootstrap, configure_tui_memory

    project_dir = _configure_project(monkeypatch, tmp_path)
    cfg = Config()
    cfg.tui.default_model = "test-model"
    cfg.tui.memory = "session"
    cfg.agent.summarization.policy = "none"
    result = await bootstrap(cfg)
    try:
        assert result.agent.memory._mgr.store.path == str(
            project_dir / "sessions" / f"{result.session_id}-memory.db"
        )
        new_id = "22222222-2222-4222-8222-222222222222"
        new_db = project_dir / "sessions" / f"{new_id}.db"
        configure_tui_memory(result.agent, cfg, agent_db=new_db, session_id=new_id)
        assert result.agent.memory._mgr.store.path == str(
            project_dir / "sessions" / f"{new_id}-memory.db"
        )
    finally:
        if result.session_manager is not None:
            result.session_manager.close()


def test_session_manager_ignores_and_deletes_memory_sidecars(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    from nemo_oo_agents.storage import SQLiteStorageManager

    project_dir = _configure_project(monkeypatch, tmp_path)
    session_id = "11111111-1111-4111-8111-111111111111"
    sessions_dir = project_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorageManager(sessions_dir / f"{session_id}.db", check_same_thread=False)
    sm = SessionManager(storage=storage, session_id=session_id, model="m", agent_cls="A")
    sm.close()
    sidecar = sessions_dir / f"{session_id}-memory.db"
    sidecar.write_text("memory sidecar")

    assert SessionManager.find_by_prefix(session_id[:8]) == [session_id]
    assert [m.id for m in SessionManager.list_sessions()] == [session_id]

    assert SessionManager.delete_session(session_id) is True
    assert not (sessions_dir / f"{session_id}.db").exists()
    assert not sidecar.exists()


@pytest.mark.asyncio
async def test_memory_command_on_off_persists_to_settings_yaml_and_reloads(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.bootstrap import bootstrap, resolve_tui_memory_scope
    from nemo_oo_agents_cli.tui.commands import CommandRegistry
    from nemo_oo_agents_cli.tui.settings import SETTINGS_FILENAME

    project_dir = _configure_project(monkeypatch, tmp_path)
    agent_key = "nemo_oo_agents_cli.tui.agent:TUIAgent"
    cfg = Config()
    cfg.tui.default_model = "test-model"
    cfg.agent.summarization.policy = "none"
    result = await bootstrap(cfg)
    try:
        registry = CommandRegistry(
            config=cfg.tui,
            agent=result.agent,
            frontend=object(),
            skills_dirs=[],
            session_manager=result.session_manager,
            root_config=cfg,
        )
        cmd = registry._commands["memory"]

        on_result = await cmd.execute(["on"])
        assert on_result.success
        assert cfg.tui.memory == "session"
        assert cfg.tui.memory_agents[agent_key] == "session"
        assert result.agent.memory._mgr.store.path == str(
            project_dir / "sessions" / f"{result.session_id}-memory.db"
        )

        # Persistence lands in settings.yaml — the file the loader actually reads —
        # NOT the phantom config.toml that no loader reads.
        settings_file = project_dir / SETTINGS_FILENAME
        assert settings_file.exists()
        assert not (project_dir / "config.toml").exists()

        # Round-trip: a fresh Config.load() (which applies settings.yaml) sees the
        # preference, so it survives the restart the command prompts for.
        reloaded = Config.load()
        assert reloaded.tui.memory_agents.get(agent_key) == "session"
        assert resolve_tui_memory_scope(result.agent, reloaded) == "session"

        off_result = await cmd.execute(["off"])
        assert off_result.success
        assert cfg.tui.memory == "off"
        assert cfg.tui.memory_agents[agent_key] == "off"
        assert not hasattr(result.agent, "memory")

        reloaded_off = Config.load()
        assert reloaded_off.tui.memory_agents.get(agent_key) == "off"
        assert resolve_tui_memory_scope(result.agent, reloaded_off) == "off"
    finally:
        if result.session_manager is not None:
            result.session_manager.close()


@pytest.mark.asyncio
async def test_bootstrap_rejects_memory_path_escape(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.bootstrap import bootstrap

    _configure_project(monkeypatch, tmp_path)
    cfg = Config()
    cfg.tui.default_model = "test-model"
    cfg.tui.memory = "session"
    cfg.tui.memory_path = tmp_path / "outside.db"
    cfg.agent.summarization.policy = "none"

    result = await bootstrap(cfg)
    try:
        assert not hasattr(result.agent, "memory")
        assert any(
            "tui.memory_path must be relative" in getattr(m, "content", "") for m in result.messages
        )
    finally:
        if result.session_manager is not None:
            result.session_manager.close()


@pytest.mark.asyncio
async def test_bootstrap_rejects_memory_path_parent_escape(tmp_path, monkeypatch):
    from nemo_oo_agents_cli.tui.bootstrap import bootstrap

    _configure_project(monkeypatch, tmp_path)
    cfg = Config()
    cfg.tui.default_model = "test-model"
    cfg.tui.memory = "session"
    cfg.tui.memory_path = Path("../outside.db")
    cfg.agent.summarization.policy = "none"

    result = await bootstrap(cfg)
    try:
        assert not hasattr(result.agent, "memory")
        assert any(
            "tui.memory_path must stay under the project directory" in getattr(m, "content", "")
            for m in result.messages
        )
    finally:
        if result.session_manager is not None:
            result.session_manager.close()
