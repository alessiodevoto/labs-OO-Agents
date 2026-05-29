# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI Configuration — inspect and manage NeMo OO Agents TUI settings."""

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from nemo_oo_agents.skill import Skill, slash_command


class TuiConfigurationSkill(Skill):
    """Inspect and manage TUI configuration: config file, external skills, libs, models.

    The TUI reads configuration from ``.nemo_oo_agents/config.toml`` in the
    project root. This skill provides ``/config`` to view and edit settings,
    and documents the full schema so the agent can answer questions.

    ## Config file

    Location: ``<project-root>/.nemo_oo_agents/config.toml``

    ```toml
    [tui]
    model = "claude-opus-4-8"        # LLM model (litellm or unifiedllm alias)
    python = false                    # Show Python execution panels
    vi = false                        # Vi keybindings
    libs_dirs = ["/path/to/skills"]   # External library skill directories
    trace = ".nemo_oo_agents/traces"  # Trace output directory
    ```

    ## External skills libraries (libs_dirs)

    ``libs_dirs`` is a TOML array of absolute paths. Each subdirectory with a
    ``pyproject.toml`` is loaded as a library skill (available as ``self.<name>``).

    Structure of a skills repo::

        my-skills/
        ├── gitlab_ci/
        │   ├── pyproject.toml   # [project.entry-points."nemo_oo_agents.skills"]
        │   ├── __init__.py      # exports a Skill subclass
        │   └── ...
        └── worktrees/
            ├── pyproject.toml
            └── __init__.py

    Each package's ``pyproject.toml`` declares its registry name::

        [project.entry-points."nemo_oo_agents.skills"]
        "nvzurich.gitlab_ci" = "gitlab_ci:GitLabCISkill"

    After adding ``libs_dirs``, restart the TUI. Skills appear as
    ``self.<lib_name>`` automatically. Use ``/skills`` to verify.

    ## SKILL.md-based slash commands (skills_dirs)

    Separate from libs, the TUI scans these directories for Markdown-based
    user-invocable commands (SKILL.md with YAML frontmatter):

    - ``.cursor/skills/``, ``.claude/skills/``, ``.claude/commands/``
    - ``tui/skills/``, ``~/.claude/skills/``, ``~/.claude/commands/``

    ## CLI flags (override config.toml)

    ``--model``, ``--libs-dir``, ``--skills-dir``, ``--trace``, ``--no-trace``,
    ``--vi``, ``--python``, ``--agent <module:Class>``

    ## Environment variables

    - ``NEMO_OO_PROJECT_DIR`` — override project directory
    - ``NEMO_OO_USER_DIR`` — override user config dir

    ## Hot-reload

    After editing a library's source code::

        await self.libs.reload("library_name")
    """

    @slash_command(
        "config",
        argument_hint="<action> [key] [value]",
        completions=("show", "set", "libs", "skills", "path"),
    )
    async def config_command(
        self,
        action: Literal["show", "set", "libs", "skills", "path"] = "show",
        key: str = "",
        value: str = "",
    ) -> str:
        """Show or modify TUI configuration.

        /config          — show current effective configuration
        /config libs     — list libs_dirs and discovered libraries
        /config skills   — list skills_dirs and discovered skills
        /config set <k> <v> — update a key in config.toml
        /config path     — show config file path
        """
        if action == "show":
            return self._show_config()
        elif action == "libs":
            return self._show_libs()
        elif action == "skills":
            return self._show_skills()
        elif action == "set":
            return self._set_config(f"{key} {value}".strip())
        elif action == "path":
            return f"Config file: {self._config_path()}"
        return "Unreachable"

    def _config_path(self) -> Path:
        """Return the path to the project config.toml."""
        from nemo_oo_agents.paths import get_project_dir

        return get_project_dir("config.toml")

    def _load_raw(self) -> dict[str, Any]:
        """Load and return the raw config.toml contents."""
        path = self._config_path()
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)

    def _show_config(self) -> str:
        """Format current config for display."""
        path = self._config_path()
        if not path.exists():
            return (
                f"No config file exists yet at `{path}`. The TUI is running on defaults.\n\n"
                "To create one, edit the file directly or use `/config set <key> <value>`.\n"
                "Available keys: `model`, `python`, `vi`, `libs_dirs`, `trace`.\n\n"
                "Example: `/config set model claude-opus-4-8`"
            )
        content = path.read_text().rstrip()
        return (
            f"Current TUI configuration (`{path}`):\n\n"
            f"```toml\n{content}\n```\n\n"
            "To change a setting: `/config set <key> <value>` (rewrites this file).\n"
            'To add an external skills library: `/config set libs_dirs ["/path/to/skills"]`\n'
            "Changes take effect on TUI restart."
        )

    def _show_libs(self) -> str:
        """List libs_dirs and what libraries are found in each."""
        data = self._load_raw()
        libs_dirs = data.get("tui", {}).get("libs_dirs", [])
        if not libs_dirs:
            return (
                "No external library directories are configured.\n\n"
                "To point the TUI at an external skills repo, add `libs_dirs` to "
                "`.nemo_oo_agents/config.toml`:\n\n"
                '```toml\n[tui]\nlibs_dirs = ["/absolute/path/to/skills-repo"]\n```\n\n'
                "Each subdirectory with a `pyproject.toml` becomes a loadable skill "
                "(available as `self.<name>`). The `pyproject.toml` must declare an "
                'entry-point under `[project.entry-points."nemo_oo_agents.skills"]`.\n\n'
                "After editing config.toml, restart the TUI to pick up the new libraries."
            )

        lines = ["The following library directories are configured:\n"]
        for d in libs_dirs:
            p = Path(d)
            if not p.exists():
                lines.append(f"- `{d}` ⚠️ directory does not exist — check the path")
                continue
            libs = sorted(
                sub.name
                for sub in p.iterdir()
                if sub.is_dir()
                and not sub.name.startswith(".")
                and (sub / "pyproject.toml").exists()
            )
            if libs:
                lines.append(f"- `{d}` ({len(libs)} libraries)")
                for lib in libs:
                    lines.append(f"  - {lib}")
            else:
                lines.append(
                    f"- `{d}` (no valid library packages found — need pyproject.toml in subdirs)"
                )

        lines.append("")
        lines.append(
            "To add another directory: edit `.nemo_oo_agents/config.toml` and append to the `libs_dirs` array."
        )
        lines.append('To reload a library after code changes: `await self.libs.reload("<name>")`')
        lines.append(
            'To activate a discovered but inactive library: `self.skills.activate(["<name>"])`'
        )
        return "\n".join(lines)

    def _show_skills(self) -> str:
        """List skills_dirs and discovered SKILL.md files."""
        from nemo_oo_agents.paths import find_project_root

        defaults = [
            Path(".cursor/skills"),
            Path(".claude/skills"),
            Path(".claude/commands"),
            Path("tui/skills"),
            Path.home() / ".claude" / "skills",
            Path.home() / ".claude" / "commands",
        ]
        root = find_project_root()
        lines = ["Discovered SKILL.md-based slash commands:\n"]
        found_any = False
        for d in defaults:
            resolved = d if d.is_absolute() else root / d
            if not resolved.exists():
                continue
            skills = sorted(
                sub.name
                for sub in resolved.iterdir()
                if sub.is_dir() and ((sub / "SKILL.md").exists() or (sub / "skill.md").exists())
            )
            if skills:
                found_any = True
                lines.append(f"- `{d}` ({len(skills)} skills)")
                for s in skills:
                    lines.append(f"  - /{s}")
        if not found_any:
            lines.append("No SKILL.md-based commands found in any default directory.")

        lines.append("")
        lines.append(
            "These are Markdown-based skills (not Python). Each is a directory with a SKILL.md file"
        )
        lines.append(
            "containing YAML frontmatter (`name`, `description`) and a body that becomes the prompt."
        )
        lines.append("")
        lines.append("To create a new one: make a directory in `.claude/commands/<name>/SKILL.md`.")
        lines.append(
            'To create a Python-based skill with methods and slash commands, use `self.libs.scaffold("<name>")`'
        )
        lines.append("and register it in `libs_dirs` (see `/config libs`).")
        return "\n".join(lines)

    def _set_config(self, args: str) -> str:
        """Set a config key by rewriting config.toml."""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            return "Usage: /config set <key> <value>\nExample: /config set model claude-opus-4-8"

        key, value = parts
        path = self._config_path()

        if path.exists():
            content = path.read_text()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = "# NeMo OO Agents TUI configuration\n\n[tui]\n"

        # Try to replace existing key under [tui]
        pattern = rf"^(\s*){re.escape(key)}\s*=.*$"
        # Detect value type: bool, int, or string
        formatted = self._format_value(value)
        new_line = f"{key} = {formatted}"

        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
        else:
            # Append after [tui] section
            if "[tui]" not in content:
                content = content.rstrip() + "\n\n[tui]\n"
            content = content.rstrip() + f"\n{new_line}\n"

        path.write_text(content)
        return f"Set `{key} = {formatted}` in {path}\n⚠️ Restart TUI for changes to take effect."

    @staticmethod
    def _format_value(raw: str) -> str:
        """Format a raw string value for TOML output."""
        low = raw.lower()
        if low in ("true", "false"):
            return low
        try:
            int(raw)
            return raw
        except ValueError:
            pass
        # Strip surrounding quotes if user typed them
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        return f'"{raw}"'
