# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``Config.load`` skills_dirs ordering.

The scan order matters: ``SkillManager.install()`` attaches a skill the
first time it sees a given attribute name, then skips subsequent matches.
So whichever directory appears first in ``cfg.tui.skills_dirs`` wins.

Intended precedence:

    1. user-explicit ``--skills-dir`` values
    2. entry-point-discovered dirs (package-provided, e.g. ``wtf-issues``)
    3. default locations (``~/.claude/commands``, ``.claude/skills``, …)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from nemo_oo_agents_cli.tui.config import Config


def _skill_dir(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "dummy-skill").mkdir(exist_ok=True)
    (d / "dummy-skill" / "SKILL.md").write_text("---\nname: dummy\ndescription: x\n---\n\nbody")
    return d


def test_explicit_skills_dir_precedes_defaults(tmp_path, monkeypatch):
    """--skills-dir comes before the default ~/.claude/commands etc."""
    explicit = _skill_dir(tmp_path, "user-explicit")
    default = _skill_dir(tmp_path, "fake-home/.claude/commands")

    # Force cwd and $HOME defaults to point at existing dirs for the test
    monkeypatch.chdir(tmp_path / "fake-home")
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    cfg = Config.load(skills_dir=[explicit])
    dirs = cfg.tui.skills_dirs
    assert explicit in dirs
    assert default in dirs
    assert dirs.index(explicit) < dirs.index(default), (
        "user --skills-dir must appear before default dirs"
    )


def test_entry_point_dirs_precede_defaults(tmp_path, monkeypatch):
    """Entry-point-discovered dirs come before the default grab-bag."""
    entry_point = _skill_dir(tmp_path, "pkg-provided")
    default = _skill_dir(tmp_path, "fake-home/.claude/commands")

    monkeypatch.chdir(tmp_path / "fake-home")
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    # Fake the entry-point machinery to return a single dir
    fake_ep = MagicMock()
    fake_ep.load.return_value = lambda: str(entry_point)
    with patch("importlib.metadata.entry_points", return_value=[fake_ep]):
        cfg = Config.load()

    dirs = cfg.tui.skills_dirs
    assert entry_point in dirs
    assert default in dirs
    assert dirs.index(entry_point) < dirs.index(default), (
        "entry-point dirs must appear before default dirs"
    )


def test_explicit_precedes_entry_point(tmp_path, monkeypatch):
    """--skills-dir beats entry-point discovery when both point somewhere."""
    explicit = _skill_dir(tmp_path, "user-explicit")
    entry_point = _skill_dir(tmp_path, "pkg-provided")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    fake_ep = MagicMock()
    fake_ep.load.return_value = lambda: str(entry_point)
    with patch("importlib.metadata.entry_points", return_value=[fake_ep]):
        cfg = Config.load(skills_dir=[explicit])

    dirs = cfg.tui.skills_dirs
    assert dirs.index(explicit) < dirs.index(entry_point)


def test_string_skills_dir_not_iterated_as_characters(tmp_path, monkeypatch):
    """A single string passed as ``skills_dir=`` is treated as one path,
    not iterated character-by-character (``list("/path")`` → ['/', 'p',
    'a', …] bug from the pre-fix code)."""
    explicit = _skill_dir(tmp_path, "single-string")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Pass a bare string (not a list) as skills_dir.
    cfg = Config.load(skills_dir=str(explicit))

    assert explicit in cfg.tui.skills_dirs
    # No character-fragments should show up
    assert not any(len(str(d)) == 1 for d in cfg.tui.skills_dirs)


def test_same_dir_passed_twice_is_deduped(tmp_path, monkeypatch):
    """A dir passed via --skills-dir and also registered as an entry point
    appears only once (via --skills-dir) in the final list."""
    shared = _skill_dir(tmp_path, "shared")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    fake_ep = MagicMock()
    fake_ep.load.return_value = lambda: str(shared)
    with patch("importlib.metadata.entry_points", return_value=[fake_ep]):
        cfg = Config.load(skills_dir=[shared])

    assert cfg.tui.skills_dirs.count(shared) == 1
