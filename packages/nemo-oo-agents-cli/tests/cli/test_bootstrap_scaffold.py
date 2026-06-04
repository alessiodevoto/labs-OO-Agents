# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for project-dir scaffolding on first run.

These cover ``_scaffold_project_dir``, which writes the starter
``config.toml`` the first time the TUI launches in a project.  The template
contains literal braces (e.g. ``${MAAS_API_KEY}`` in the MCP example), so it
must not be expanded with ``str.format()`` — that raises ``KeyError`` on the
unrelated brace and crashes startup before any config is ever written.
"""

from nemo_oo_agents_cli.tui.bootstrap import _CONFIG_TOML_TEMPLATE, _scaffold_project_dir
from nemo_oo_agents_cli.tui.config import Config


def test_scaffold_writes_config_with_model_and_preserves_literal_braces(tmp_path, monkeypatch):
    """First-run scaffold writes config.toml without choking on literal braces."""
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path / ".nemo_oo_agents"))

    config = Config()
    config.tui.default_model = "nvidia/llama-3.3"

    _scaffold_project_dir(config)

    config_path = tmp_path / ".nemo_oo_agents" / "config.toml"
    assert config_path.exists()
    written = config_path.read_text()

    # The {default_model} placeholder is substituted...
    assert "nvidia/llama-3.3" in written
    assert "{default_model}" not in written
    # ...while literal braces from the MCP example survive verbatim.
    assert "${MAAS_API_KEY}" in written


def test_scaffold_is_idempotent(tmp_path, monkeypatch):
    """An existing config.toml is never overwritten."""
    project_dir = tmp_path / ".nemo_oo_agents"
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(project_dir))
    project_dir.mkdir(parents=True)
    config_path = project_dir / "config.toml"
    config_path.write_text("# user edits\n")

    _scaffold_project_dir(Config())

    assert config_path.read_text() == "# user edits\n"


def test_template_has_only_the_default_model_field():
    """Guard: {default_model} is the only single-brace field in the template.

    If a future edit adds another ``{field}`` it would silently break the
    plain .replace() substitution, so flag it here.
    """
    # Drop ${...} shell-style refs, then drop the known placeholder, then
    # assert no other single-brace {token} survives.
    import re

    stripped = re.sub(r"\$\{[^}]*\}", "", _CONFIG_TOML_TEMPLATE)
    stripped = stripped.replace("{default_model}", "")
    assert re.search(r"(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}", stripped) is None
