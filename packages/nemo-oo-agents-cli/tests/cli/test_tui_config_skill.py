# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the TUI ``/config`` slash-command skill."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from nemo_oo_agents_cli.tui.config import Config, TUIConfig
from nemo_oo_agents_cli.tui.tui_config_skill import TuiConfigurationSkill


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """Isolate layered settings paths from developer/project config."""
    user = tmp_path / "user"
    user.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NEMO_OO_USER_DIR", str(user))
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(project))
    monkeypatch.delenv("NEMO_OO_SETTINGS", raising=False)
    return project


def _settings_path(project_dir):
    return project_dir / "settings.yaml"


def test_config_set_dotted_tui_key_writes_nested_settings(project_dir):
    """Dotted ``tui.*`` keys write nested YAML, not literal dotted keys."""
    skill = TuiConfigurationSkill()

    result = skill._set_config("tui.default_model gpt-5.5")
    data = yaml.safe_load(_settings_path(project_dir).read_text())

    assert "Set `tui.default_model = 'gpt-5.5'`" in result
    assert data == {"tui": {"default_model": "gpt-5.5"}}
    assert "tui.default_model" not in data["tui"]
    assert Config.load().tui.default_model == "gpt-5.5"


def test_config_set_friendly_model_key_still_writes_tui_default_model(project_dir):
    """The friendly ``model`` alias still writes ``tui.default_model``."""
    skill = TuiConfigurationSkill()

    result = skill._set_config("model gpt-5.5")
    data = yaml.safe_load(_settings_path(project_dir).read_text())

    assert "Set `tui.default_model = 'gpt-5.5'`" in result
    assert data == {"tui": {"default_model": "gpt-5.5"}}
    assert Config.load().tui.default_model == "gpt-5.5"


def test_config_set_dotted_key_updates_nested_existing_section(project_dir):
    """Dotted paths update deep nested settings without clobbering siblings."""
    _settings_path(project_dir).write_text(
        "tui:\n  default_model: old-model\nagent:\n  summarization:\n    window_size: 50\n"
    )
    skill = TuiConfigurationSkill()

    result = skill._set_config("agent.summarization.window_size 99")
    data = yaml.safe_load(_settings_path(project_dir).read_text())

    assert "Set `agent.summarization.window_size = 99`" in result
    assert data["tui"]["default_model"] == "old-model"
    assert data["agent"]["summarization"]["window_size"] == 99
    assert Config.load().agent.summarization.window_size == 99


def test_config_set_invalid_dotted_key_returns_error(project_dir):
    """Malformed dotted keys return a user-facing error instead of raising."""
    skill = TuiConfigurationSkill()

    result = skill._set_config("tui..default_model gpt-5.5")

    assert result == "Error: Invalid config key: 'tui..default_model'"
    assert not _settings_path(project_dir).exists()


def _attach_runtime_config(skill: TuiConfigurationSkill, config: TUIConfig, vars=None) -> None:
    agent = SimpleNamespace(
        _command_registry=SimpleNamespace(config=config),
        vars={} if vars is None else vars,
    )
    skill.attach(agent)


def test_config_save_project_writes_safe_runtime_settings(project_dir):
    skill = TuiConfigurationSkill()
    _attach_runtime_config(
        skill,
        TUIConfig(
            default_model="gpt-5.5",
            show_python=True,
            goal_mode=True,
            keep_going=True,
            keep_going_model="audit-model",
            toolbar_snippet="short_model",
        ),
    )

    result = skill._save_config("")
    data = yaml.safe_load(_settings_path(project_dir).read_text())

    assert "Saved current TUI settings to project config" in result
    assert data == {
        "tui": {
            "default_model": "gpt-5.5",
            "show_python": True,
            "goal_mode": True,
            "keep_going": True,
            "keep_going_model": "audit-model",
            "toolbar_snippet": "short_model",
        }
    }


def test_config_save_user_writes_user_settings(project_dir):
    from nemo_oo_agents.paths import get_user_dir

    skill = TuiConfigurationSkill()
    _attach_runtime_config(skill, TUIConfig(default_model="gpt-5.5"))

    result = skill._save_config("user")
    user_settings = get_user_dir("settings.yaml")
    data = yaml.safe_load(user_settings.read_text())

    assert "Saved current TUI settings to user config" in result
    assert not _settings_path(project_dir).exists()
    assert data["tui"]["default_model"] == "gpt-5.5"


def test_config_save_dry_run_does_not_write_file_or_directory(tmp_path, monkeypatch):
    """Verify dry-run previews settings without creating files or directories."""
    project_dir = tmp_path / "missing-project"
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(project_dir))
    skill = TuiConfigurationSkill()
    _attach_runtime_config(skill, TUIConfig(default_model="gpt-5.5", show_python=True))

    result = skill._save_config("--dry-run")

    assert "Would save current TUI settings" in result
    assert "default_model: gpt-5.5" in result
    assert not project_dir.exists()


def test_config_save_prefers_sticky_keep_going_agent_vars(project_dir):
    skill = TuiConfigurationSkill()
    _attach_runtime_config(
        skill,
        TUIConfig(keep_going=False, keep_going_model="config-model"),
        vars={"tui_keep_going": True, "tui_keep_going_model": "sticky-model"},
    )

    skill._save_config("")
    data = yaml.safe_load(_settings_path(project_dir).read_text())

    assert data["tui"]["keep_going"] is True
    assert data["tui"]["keep_going_model"] == "sticky-model"


def test_config_save_invalid_args_returns_usage(project_dir):
    skill = TuiConfigurationSkill()
    _attach_runtime_config(skill, TUIConfig())

    result = skill._save_config("global")

    assert result == "Usage: /config save [project|user] [--dry-run]"
    assert not _settings_path(project_dir).exists()
