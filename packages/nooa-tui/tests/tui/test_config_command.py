# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``nooa config`` subcommand."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from nooa_cli.commands.config import command


@pytest.fixture
def _isolated_env(tmp_path, monkeypatch):
    """Strip env vars, isolate user / project dirs, stub bundled providers empty.

    Tests that want a bundled provider re-patch
    ``nooa.llm_config.bundled_config_paths``.
    """
    monkeypatch.delenv("NEMO_OO_LLM_CONFIG", raising=False)
    monkeypatch.setattr("nooa.llm_config.bundled_config_paths", lambda: [])
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    monkeypatch.setenv("NEMO_OO_USER_DIR", str(user_dir))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(project_dir))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return tmp_path


def _stub_bundled(monkeypatch, *paths: Path) -> None:
    """Make ``bundled_config_paths`` return the given synthetic paths."""
    monkeypatch.setattr(
        "nooa.llm_config.bundled_config_paths",
        lambda: list(paths),
    )


class TestShowCommand:
    """`nooa config show` prints the resolved chain."""

    def test_show_prints_all_section_headers(self, _isolated_env):
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0, result.output
        assert "Bundled defaults:" in result.output
        assert "User config" in result.output
        assert "NEMO_OO_LLM_CONFIG" in result.output
        assert "Project config" in result.output
        assert "Total:" in result.output

    def test_show_bundled_none_registered_message(self, _isolated_env):
        """With no bundled-defaults providers installed, ``show`` says so."""
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0
        assert "none registered" in result.output

    def test_show_lists_bundled_providers(self, _isolated_env, tmp_path, monkeypatch):
        first = tmp_path / "first.yaml"
        first.write_text("models:\n  alias-a: {model_name: x}\n")
        second = tmp_path / "second.yaml"
        second.write_text("models:\n  alias-b: {model_name: y}\n")
        _stub_bundled(monkeypatch, first, second)
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0, result.output
        assert str(first) in result.output
        assert str(second) in result.output
        assert "(1 aliases)" in result.output

    def test_show_lists_user_config_when_present(self, _isolated_env):
        user_path = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        user_path.write_text("models:\n  alias: {model_name: x}\n")
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0
        assert str(user_path) in result.output
        assert "present" in result.output

    def test_show_lists_env_paths(self, _isolated_env, tmp_path, monkeypatch):
        env_yaml = tmp_path / "env-config.yaml"
        env_yaml.write_text("models:\n  alias: {model_name: x}\n")
        monkeypatch.setenv("NEMO_OO_LLM_CONFIG", str(env_yaml))
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0
        assert str(env_yaml.resolve()) in result.output

    def test_show_lists_project_config_when_present(self, _isolated_env):
        project_path = Path(os.environ["NEMO_OO_PROJECT_DIR"]) / "llm_config.yaml"
        project_path.write_text("models:\n  proj-alias: {model_name: y}\n")
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0
        assert str(project_path) in result.output
        assert "present" in result.output

    def test_show_total_reflects_loaded_aliases(self, _isolated_env):
        user_path = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        user_path.write_text("models:\n  alpha: {model_name: a}\n  beta: {model_name: b}\n")
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0
        assert "Total: 2 model aliases" in result.output

    def test_show_marks_deduplicated_layer(self, _isolated_env, monkeypatch):
        """If a candidate layer's file is also reachable via a
        higher-priority layer, the lower one is annotated as
        deduplicated rather than silently misreported."""
        user_path = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        user_path.write_text("models:\n  alias: {model_name: x}\n")
        # Point NEMO_OO_LLM_CONFIG at the same file → env-var slot
        # wins, user slot deduplicates out.
        monkeypatch.setenv("NEMO_OO_LLM_CONFIG", str(user_path))
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0, result.output
        assert "deduplicated" in result.output
        # The total is 1 — the file is loaded once, not twice.
        assert "Total: 1 model aliases" in result.output

    def test_show_lists_referenced_api_key_env_vars(self, _isolated_env, monkeypatch):
        """The ``API key env vars referenced`` block names each var and its set/unset state."""
        user_path = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        user_path.write_text(
            "models:\n"
            "  set-keyed: {model_name: a, api_key_env: TEST_KEY_PRESENT}\n"
            "  unset-keyed: {model_name: b, api_key_env: TEST_KEY_ABSENT}\n"
        )
        monkeypatch.setenv("TEST_KEY_PRESENT", "sk-yes")
        monkeypatch.delenv("TEST_KEY_ABSENT", raising=False)
        runner = CliRunner()
        result = runner.invoke(command, ["show"])
        assert result.exit_code == 0, result.output
        assert "API key env vars referenced:" in result.output
        assert "TEST_KEY_PRESENT" in result.output
        assert "(set)" in result.output
        assert "TEST_KEY_ABSENT" in result.output
        assert "NOT SET" in result.output


class TestPathCommand:
    """`nooa config path` prints the user-level YAML path."""

    def test_path_prints_user_dir_target(self, _isolated_env):
        runner = CliRunner()
        result = runner.invoke(command, ["path"])
        assert result.exit_code == 0
        expected = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        assert str(expected) in result.output.strip()


class TestEjectCommand:
    """`nooa config eject` copies the bundled YAML to the user-level path."""

    def test_eject_writes_file(self, _isolated_env, tmp_path, monkeypatch):
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models:\n  alias: {model_name: m}\n")
        _stub_bundled(monkeypatch, bundled)
        runner = CliRunner()
        result = runner.invoke(command, ["eject"])
        assert result.exit_code == 0, result.output
        target = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        assert target.exists()
        assert "models:" in target.read_text()
        assert f"Wrote {target}" in result.output

    def test_eject_creates_parent_dir(self, _isolated_env, tmp_path, monkeypatch):
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models: {}\n")
        _stub_bundled(monkeypatch, bundled)
        # Point at a nested non-existing dir under the isolated tmp path.
        nested = tmp_path / "a" / "b" / "c"
        monkeypatch.setenv("NEMO_OO_USER_DIR", str(nested))
        runner = CliRunner()
        result = runner.invoke(command, ["eject"])
        assert result.exit_code == 0, result.output
        assert (nested / "llm_config.yaml").exists()

    def test_eject_refuses_overwrite_without_force(self, _isolated_env, tmp_path, monkeypatch):
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models: {}\n")
        _stub_bundled(monkeypatch, bundled)
        target = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        target.write_text("# pre-existing content\n")

        runner = CliRunner()
        result = runner.invoke(command, ["eject"])
        assert result.exit_code == 1
        assert "Refusing to overwrite" in result.output
        # File still has the original content (untouched)
        assert target.read_text() == "# pre-existing content\n"

    def test_eject_force_overwrites(self, _isolated_env, tmp_path, monkeypatch):
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models:\n  alias: {model_name: m}\n")
        _stub_bundled(monkeypatch, bundled)
        target = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        target.write_text("# pre-existing\n")

        runner = CliRunner()
        result = runner.invoke(command, ["eject", "--force"])
        assert result.exit_code == 0, result.output
        assert "models:" in target.read_text()

    def test_eject_warns_when_env_var_set(self, _isolated_env, tmp_path, monkeypatch):
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models: {}\n")
        _stub_bundled(monkeypatch, bundled)
        env_yaml = tmp_path / "stale.yaml"
        env_yaml.write_text("models: {}\n")
        monkeypatch.setenv("NEMO_OO_LLM_CONFIG", str(env_yaml))

        runner = CliRunner()
        result = runner.invoke(command, ["eject"])
        assert result.exit_code == 0, result.output
        assert "NEMO_OO_LLM_CONFIG" in result.output

    def test_eject_no_provider_registered_is_error(self, _isolated_env):
        """With no bundled-defaults provider installed, eject errors out."""
        runner = CliRunner()
        result = runner.invoke(command, ["eject"])
        assert result.exit_code == 1
        assert "No bundled-defaults provider is installed" in result.output

    def test_eject_multiple_providers_is_error(self, _isolated_env, tmp_path, monkeypatch):
        """With multiple providers registered, eject refuses to pick one."""
        first = tmp_path / "first.yaml"
        first.write_text("models: {}\n")
        second = tmp_path / "second.yaml"
        second.write_text("models: {}\n")
        _stub_bundled(monkeypatch, first, second)
        runner = CliRunner()
        result = runner.invoke(command, ["eject"])
        assert result.exit_code == 1
        assert "Multiple bundled-defaults providers" in result.output

    def test_eject_refuses_symlink_target(self, _isolated_env, tmp_path, monkeypatch):
        """A symlink at the target path is refused even with --force.

        Managed-dotfiles users routinely symlink ~/.config files. Following
        the symlink and clobbering the upstream target would be surprising.
        """
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models: {}\n")
        _stub_bundled(monkeypatch, bundled)
        target = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        upstream = tmp_path / "dotfiles_config.yaml"
        upstream.write_text("# upstream content\n")
        target.symlink_to(upstream)

        runner = CliRunner()
        result = runner.invoke(command, ["eject", "--force"])
        assert result.exit_code == 1
        assert "symlink" in result.output.lower()
        # Upstream untouched.
        assert upstream.read_text() == "# upstream content\n"

    def test_eject_refuses_directory_target(self, _isolated_env, tmp_path, monkeypatch):
        """A directory at the target path is refused even with --force."""
        bundled = tmp_path / "bundled.yaml"
        bundled.write_text("models: {}\n")
        _stub_bundled(monkeypatch, bundled)
        target = Path(os.environ["NEMO_OO_USER_DIR"]) / "llm_config.yaml"
        target.mkdir()

        runner = CliRunner()
        result = runner.invoke(command, ["eject", "--force"])
        assert result.exit_code == 1
        assert "directory" in result.output.lower()


class TestShowSettingsSecrets:
    """`nooa config show` reports settings.yaml + secrets.yaml layers."""

    @pytest.fixture(autouse=True)
    def _clear_layer_env(self, monkeypatch):
        monkeypatch.delenv("NEMO_OO_SETTINGS", raising=False)
        monkeypatch.delenv("NEMO_OO_SECRETS", raising=False)

    def test_show_prints_settings_and_secrets_headers(self, _isolated_env):
        result = CliRunner().invoke(command, ["show"])
        assert result.exit_code == 0, result.output
        assert "Settings (settings.yaml):" in result.output
        assert "Secrets (secrets.yaml):" in result.output
        assert "NEMO_OO_SETTINGS (override):" in result.output
        assert "NEMO_OO_SECRETS (override):" in result.output

    def test_settings_summary_shows_section_key_counts(self, _isolated_env):
        user_dir = Path(os.environ["NEMO_OO_USER_DIR"])
        (user_dir / "settings.yaml").write_text(
            "tui:\n  default_model: foo\n  vi_mode: true\nagent:\n  working_dir: /tmp\n"
        )
        result = CliRunner().invoke(command, ["show"])
        assert result.exit_code == 0
        assert "tui: 2 keys" in result.output
        assert "agent: 1 keys" in result.output

    def test_secrets_values_are_redacted(self, _isolated_env):
        user_dir = Path(os.environ["NEMO_OO_USER_DIR"])
        (user_dir / "secrets.yaml").write_text(
            "env:\n  NVIDIA_INTERNAL_API_KEY: sk-supersecret\n  ANTHROPIC_API_KEY: sk-ant-xyz\n"
        )
        result = CliRunner().invoke(command, ["show"])
        assert result.exit_code == 0
        # Key names shown, values never printed.
        assert "NVIDIA_INTERNAL_API_KEY" in result.output
        assert "ANTHROPIC_API_KEY" in result.output
        assert "values redacted" in result.output
        assert "sk-supersecret" not in result.output
        assert "sk-ant-xyz" not in result.output

    def test_absent_files_marked_not_present(self, _isolated_env):
        result = CliRunner().invoke(command, ["show"])
        assert result.exit_code == 0
        # Both settings and secrets user/project layers absent.
        assert result.output.count("not present") >= 4
