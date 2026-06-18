# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the TUI ``/config`` slash-command skill."""

from __future__ import annotations

import pytest
import yaml
from nemo_oo_agents_cli.tui.config import Config
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
